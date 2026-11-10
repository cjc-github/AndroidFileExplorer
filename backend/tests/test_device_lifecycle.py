"""Regression tests for the device lifecycle endpoints.

Two real defects are locked in here:

1. A failed ``adb connect`` must NOT mark the device offline. For a USB or
   emulator serial the command is simply not applicable ("missing port in
   specification"), and taking the device out of the registry would make a
   healthy device disappear.
2. The list and the detail endpoint must agree about the device state.
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


class ConnectFailsTransport(FakeTransport):
    """A transport whose `adb connect` always fails."""

    async def connect(self, serial: str):
        return False, "missing port in specification: tcp:emulator-5554"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    transport = ConnectFailsTransport(
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


def test_failed_connect_does_not_take_the_device_offline(client):
    uid = _seed(client)

    response = client.post(f"/api/v1/devices/{uid}/connect")
    assert response.status_code == 400
    assert "missing port" in response.json()["detail"]

    detail = client.get(f"/api/v1/devices/{uid}").json()["data"]
    assert detail["status"] != "offline", (
        "a failed connect must not remove a healthy device from the registry"
    )


def test_list_and_detail_agree_about_the_state(client):
    uid = _seed(client)

    listing = client.get("/api/v1/devices").json()["data"]
    detail = client.get(f"/api/v1/devices/{uid}").json()["data"]

    assert len(listing) == 1
    assert listing[0]["status"] == detail["status"]


def test_network_devices_are_flagged(client):
    """`adb connect` only works for ip:port devices."""
    transport = client.transport
    transport._devices = [
        DeviceEntry(serial="192.168.1.9:5555", state="device", model="Wifi"),
    ]

    async def _run() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            registry = DeviceRegistry(session)
            await registry.upsert_device(transport, transport._devices[0], None)

    run_sync(_run())

    devices = client.get("/api/v1/devices").json()["data"]
    assert devices[0]["is_network_device"] is True

    transport._devices = [DeviceEntry(serial="emulator-5554", state="device")]
    run_sync(_run())
    devices = client.get("/api/v1/devices").json()["data"]
    assert devices[0]["is_network_device"] is False
