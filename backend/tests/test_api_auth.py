"""The optional API bearer token middleware.

Default configuration (no ``API_AUTH_TOKEN``) keeps the API open; a
configured token closes everything except the health probe.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.api.auth import AuthTokenMiddleware
from app.config import settings
from app.main import app as main_app


def _secure_app(token: str) -> TestClient:
    async def endpoint(_request) -> JSONResponse:
        return JSONResponse({"ok": True})

    inner = Starlette(
        routes=[
            Route("/api/v1/engines", endpoint),
            Route("/api/v1/health", endpoint),
        ]
    )
    return TestClient(AuthTokenMiddleware(inner, token))


def test_open_by_default():
    with _secure_app("") as client:
        assert client.get("/api/v1/engines").status_code == 200


def test_missing_token_is_401_when_enabled():
    with _secure_app("secret") as client:
        assert client.get("/api/v1/engines").status_code == 401
        assert "token" in client.get("/api/v1/engines").json()["detail"]


def test_wrong_token_is_401():
    with _secure_app("secret") as client:
        response = client.get(
            "/api/v1/engines", headers={"Authorization": "Bearer nope"}
        )
        assert response.status_code == 401


def test_correct_bearer_token_passes():
    with _secure_app("secret") as client:
        response = client.get(
            "/api/v1/engines", headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 200


def test_health_probe_stays_public():
    with _secure_app("secret") as client:
        assert client.get("/api/v1/health").status_code == 200


def test_query_token_is_accepted_for_anchor_downloads():
    with _secure_app("secret") as client:
        assert client.get("/api/v1/engines", params={"token": "secret"}).status_code == 200
        assert client.get("/api/v1/engines", params={"token": "nope"}).status_code == 401


def test_main_app_registers_the_middleware():
    from starlette.middleware.cors import CORSMiddleware

    from app.api.request_id import RequestIdMiddleware

    classes = [middleware.cls for middleware in main_app.user_middleware]
    assert classes[0] is CORSMiddleware, "CORS must be outermost (preflight)"
    assert AuthTokenMiddleware in classes[1:]
    # The request id must sit OUTSIDE the auth check: the auth middleware
    # answers a 401 on its own, so a request id placed inside would never
    # see the rejected request.
    assert classes.index(RequestIdMiddleware) < classes.index(AuthTokenMiddleware)


def test_unauthorized_responses_are_not_shared():
    """Two rejections must not share one response object.

    A module level JSONResponse would carry the first request's headers
    (the request id) into every later rejection.
    """
    with _secure_app("secret") as client:
        first = client.get("/api/v1/engines")
        second = client.get("/api/v1/engines")
        assert first.status_code == second.status_code == 401
        assert first.content == second.content
        assert first.headers.get("x-request-id") != second.headers.get(
            "x-request-id"
        ) or first.headers.get("x-request-id") is None


def test_default_settings_keep_the_api_open():
    assert settings.api_auth_token == ""
