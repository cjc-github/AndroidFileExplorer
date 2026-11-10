"""Regression tests for the scope of permission errors.

A protected path (``/system`` needs root) must NOT lock the whole device:
after a 403 on one path the other paths must keep working and the device
lifecycle state must stay ``online``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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
    "/": [
        FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096),
        FileEntry(name="system", path="/system", is_dir=True, size=4096),
    ],
    "/sdcard": [FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=26)],
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
    transport.permission_denied_paths = {"/system"}

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


def test_protected_path_does_not_lock_the_device(client):
    uid = _seed(client)

    denied = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/system"})
    assert denied.status_code == 403

    # The device must still be usable.
    detail = client.get(f"/api/v1/devices/{uid}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "online"

    allowed = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/sdcard"})
    assert allowed.status_code == 200
    assert allowed.json()["data"]["items"][0]["name"] == "hello.txt"


def test_search_skips_protected_directories(client):
    uid = _seed(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/search",
        params={"keyword": "hello", "path": "/"},
    )
    assert response.status_code == 200
    assert [item["name"] for item in response.json()["data"]["items"]] == ["hello.txt"]


def test_transfer_still_works_after_a_permission_error(client, tmp_path):
    uid = _seed(client)
    client.get(f"/api/v1/devices/{uid}/files", params={"path": "/system"})

    source = tmp_path / "payload.bin"
    source.write_bytes(b"still working")

    response = client.post(
        f"/api/v1/devices/{uid}/files/push",
        json={"path": "/sdcard/payload.bin", "local_path": str(source)},
    )
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True
