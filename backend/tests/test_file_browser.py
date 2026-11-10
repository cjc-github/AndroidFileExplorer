"""Regression tests for the file browser: breadcrumb and permission errors.

Two real-device findings are locked in here:

1. ``/data/local/tmp`` is readable on an unrooted device while ``/data`` and
   ``/data/local`` are not. The breadcrumb must therefore be built from the
   REQUESTED path, so the user can still navigate to a deep path whose
   parents are protected.
2. A protected path must produce a 403 that explains the cause (root
   required), not a generic failure.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.db import session as db_session
from app.main import app
from app.services.registry import build_device_uid
from app.state import get_transport_resolver
from app.transports.base import DeviceEntry, FileEntry

from .conftest import FakeTransport

TREE = {
    "/": [FileEntry(name="data", path="/data", is_dir=True, size=4096)],
    "/data/local/tmp": [
        FileEntry(name="payload.bin", path="/data/local/tmp/payload.bin", is_dir=False, size=12)
    ],
}


def run_sync(coro):
    """Run a coroutine from synchronous test code (own event loop)."""
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")],
        tree=TREE,
    )
    # /data and /data/local are protected, /data/local/tmp is readable.
    transport.permission_denied_paths = {"/data", "/data/local"}

    resolver = get_transport_resolver()
    original_local = resolver._local
    resolver._local = transport  # type: ignore[assignment]

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    async def _prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    run_sync(_prepare())

    with TestClient(app) as test_client:
        test_client.transport = transport  # type: ignore[attr-defined]
        yield test_client

    resolver._local = original_local
    run_sync(engine.dispose())


def _seed(client) -> str:
    transport = client.transport
    uid = build_device_uid(transport.kind, transport.endpoint, "emulator-5554")

    async def _run() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            registry = DeviceRegistry(session)
            await registry.upsert_device(transport, transport._devices[0], None)

    run_sync(_run())
    return uid


def test_breadcrumb_lists_every_level_of_a_deep_path(client):
    """The breadcrumb comes from the requested path, not from the parent."""
    uid = _seed(client)
    response = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/data/local/tmp"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current_path"] == "/data/local/tmp"
    assert [part["name"] for part in data["parts"]] == ["data", "local", "tmp"]
    assert [part["path"] for part in data["parts"]] == [
        "/data",
        "/data/local",
        "/data/local/tmp",
    ]
    assert data["parent_path"] == "/data/local"


def test_deep_path_is_listable_even_when_its_parents_are_protected(client):
    uid = _seed(client)
    response = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/data/local/tmp"})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["name"] for item in items] == ["payload.bin"]


def test_protected_path_returns_403_with_a_root_hint(client):
    uid = _seed(client)
    response = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/data"})
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "权限不足" in detail
    assert "root" in detail
    assert "Permission denied" in detail


def test_protected_path_does_not_change_the_device_state(client):
    """A path level error is local: the device stays online."""
    uid = _seed(client)
    client.get(f"/api/v1/devices/{uid}/files", params={"path": "/data"})

    detail = client.get(f"/api/v1/devices/{uid}").json()["data"]
    assert detail["status"] == "online"
    assert detail["last_error"] is None


def test_search_skips_protected_directories(client):
    """A search cannot walk through a protected directory.

    Starting at ``/`` the walk reaches ``/data``, the listing is denied and
    the protected subtree is skipped, so nothing is found. Starting at the
    readable directory the file is found - the same behaviour as a real
    unrooted device (verified against 192.168.114.130).
    """
    uid = _seed(client)

    from_root = client.get(
        f"/api/v1/devices/{uid}/files/search",
        params={"keyword": "payload", "path": "/"},
    )
    assert from_root.status_code == 200
    assert from_root.json()["data"]["items"] == []

    from_tmp = client.get(
        f"/api/v1/devices/{uid}/files/search",
        params={"keyword": "payload", "path": "/data/local/tmp"},
    )
    assert from_tmp.status_code == 200
    items = from_tmp.json()["data"]["items"]
    assert [item["name"] for item in items] == ["payload.bin"]
