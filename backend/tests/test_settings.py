"""Regression tests for the platform settings and the auto-root hook."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

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


class RootableTransport(FakeTransport):
    """A fake transport whose set_root succeeds and tracks calls."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.root_calls: list[tuple[str, bool]] = []

    async def set_root(self, serial: str, enabled: bool) -> tuple[bool, str]:
        self.root_calls.append((serial, enabled))
        return True, "restarting adbd"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    async def _prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    run_sync(_prepare())

    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client

    run_sync(engine.dispose())


def test_settings_default_values(client):
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sync_interval_seconds"] == 3
    assert data["auto_root"] is False
    assert data["auto_sync_seconds"] == 0


def test_settings_round_trip(client):
    payload = {"sync_interval_seconds": 10, "auto_root": True, "auto_sync_seconds": 30}
    response = client.put("/api/v1/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["data"] == payload

    # A second read sees the persisted values.
    assert client.get("/api/v1/settings").json()["data"] == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"sync_interval_seconds": 0, "auto_root": False, "auto_sync_seconds": 0},
        {"sync_interval_seconds": 61, "auto_root": False, "auto_sync_seconds": 0},
        {"sync_interval_seconds": 3, "auto_root": False, "auto_sync_seconds": -1},
    ],
)
def test_settings_reject_out_of_range_values(client, payload):
    response = client.put("/api/v1/settings", json=payload)
    assert response.status_code == 422


def test_auto_root_roots_online_devices(tmp_path):
    """With auto_root enabled, online non-rooted devices get rooted."""
    from app.api.routes.devices import run_auto_root
    from app.services.registry import DeviceRegistry
    from app.schemas.settings import SettingsInfo, SettingsUpdate
    from app.services.settings_service import SettingsService

    transport = RootableTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")],
        tree={},
    )
    resolver = get_transport_resolver()
    original_local = resolver._local
    resolver._local = transport  # type: ignore[assignment]

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'root.db'}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    async def _scenario() -> int:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with maker() as session:
            registry = DeviceRegistry(session)
            entry = transport._devices[0]
            await registry.upsert_device(transport, entry, None)

            # Enabled: the device gets rooted.
            await SettingsService(session).update(
                SettingsUpdate(sync_interval_seconds=3, auto_root=True, auto_sync_seconds=0)
            )
            count = await run_auto_root(session, resolver)
            await session.commit()
            record = await registry.get_device(
                build_device_uid(transport.kind, transport.endpoint, "emulator-5554")
            )
            assert record is not None and record.is_rooted is True
            return count

    try:
        assert run_sync(_scenario()) == 1
        assert transport.root_calls == [("emulator-5554", True)]
    finally:
        resolver._local = original_local
        run_sync(engine.dispose())


def test_auto_root_is_a_no_op_when_disabled(tmp_path):
    from app.api.routes.devices import run_auto_root
    from app.services.registry import DeviceRegistry
    from app.schemas.settings import SettingsUpdate
    from app.services.settings_service import SettingsService

    transport = RootableTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")],
        tree={},
    )
    resolver = get_transport_resolver()
    original_local = resolver._local
    resolver._local = transport  # type: ignore[assignment]

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'off.db'}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    async def _scenario() -> int:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        async with maker() as session:
            registry = DeviceRegistry(session)
            await registry.upsert_device(transport, transport._devices[0], None)
            await SettingsService(session).update(
                SettingsUpdate(sync_interval_seconds=3, auto_root=False, auto_sync_seconds=0)
            )
            return await run_auto_root(session, resolver)

    try:
        assert run_sync(_scenario()) == 0
        assert transport.root_calls == []
    finally:
        resolver._local = original_local
        run_sync(engine.dispose())
