"""Regression tests for the root escalation endpoint.

``adb root`` restarts adbd with root privileges; afterwards the protected
paths become readable. The endpoint must:
  * report the new state (is_rooted),
  * answer 400 when the device refuses (production build),
  * not change the device state when it fails.
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
from app.transports.base import DeviceEntry

from .conftest import FakeTransport


def run_sync(coro):
    return asyncio.run(coro)


class RootRefusedTransport(FakeTransport):
    """A device that refuses to run adbd as root."""

    async def adb_root(self, serial: str) -> str:
        return "adbd cannot run as root in production builds"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")],
        tree={},
    )

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


def test_try_root_marks_the_device_as_rooted(client):
    uid = _seed(client)
    assert client.get(f"/api/v1/devices/{uid}").json()["data"]["is_rooted"] is False

    response = client.post(f"/api/v1/devices/{uid}/root")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["is_rooted"] is True
    assert "root" in body["message"]

    assert client.get(f"/api/v1/devices/{uid}").json()["data"]["is_rooted"] is True


def test_try_root_on_unknown_device_is_404(client):
    response = client.post("/api/v1/devices/nope/root")
    assert response.status_code == 404


def test_refused_root_answers_400_and_keeps_the_state(client):
    uid = _seed(client)
    transport = client.transport
    transport.adb_root = RootRefusedTransport.adb_root.__get__(transport)  # type: ignore[assignment]

    response = client.post(f"/api/v1/devices/{uid}/root")
    assert response.status_code == 400
    assert "production" in response.json()["detail"].lower()

    # A refused escalation must not take the device out of the registry.
    detail = client.get(f"/api/v1/devices/{uid}").json()["data"]
    assert detail["status"] == "online"


def test_device_list_reports_the_root_flag(client):
    uid = _seed(client)
    client.post(f"/api/v1/devices/{uid}/root")

    devices = client.get("/api/v1/devices").json()["data"]
    assert devices[0]["is_rooted"] is True
