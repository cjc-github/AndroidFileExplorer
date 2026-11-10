"""Device registry: unique id stability and lifecycle state machine."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.models.device import DeviceRecord
from app.services.registry import DeviceRegistry, build_device_uid
from app.transports.base import DeviceEntry, DeviceState

from .conftest import FakeTransport


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session
    await engine.dispose()


def test_uid_is_stable_and_unique():
    first = build_device_uid("local", "local:127.0.0.1:5037", "emulator-5554")
    again = build_device_uid("local", "local:127.0.0.1:5037", "emulator-5554")
    other_serial = build_device_uid("local", "local:127.0.0.1:5037", "emulator-5556")
    other_machine = build_device_uid("remote", "192.168.1.2:12345", "emulator-5554")
    other_kind = build_device_uid("local2", "local:127.0.0.1:5037", "emulator-5554")

    assert first == again, "the same device must keep its id"
    assert len({first, other_serial, other_machine, other_kind}) == 4
    assert len(first) == 16


@pytest.mark.asyncio
async def test_upsert_keeps_the_same_uid_across_reconnects(session):
    registry = DeviceRegistry(session)
    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel")],
    )

    record = await registry.upsert_device(transport, transport._devices[0], None)
    uid = record.uid
    assert record.status == DeviceState.ONLINE.value

    # Same device, second sync.
    record_again = await registry.upsert_device(transport, transport._devices[0], None)
    assert record_again.uid == uid
    assert len(await registry.list_devices()) == 1


@pytest.mark.asyncio
async def test_device_that_disappears_becomes_offline(session):
    registry = DeviceRegistry(session)
    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device")],
    )
    record = await registry.upsert_device(transport, transport._devices[0], None)

    transport._devices = []
    await registry.sync_transport(transport, None)

    refreshed = await registry.get_device(record.uid)
    assert refreshed is not None
    assert refreshed.status == DeviceState.OFFLINE.value
    assert refreshed.last_error


@pytest.mark.asyncio
async def test_unauthorized_state_is_not_confused_with_offline(session):
    registry = DeviceRegistry(session)
    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="unauthorized")],
    )
    record = await registry.upsert_device(transport, transport._devices[0], None)
    assert record.status == DeviceState.UNAUTHORIZED.value

    transport._devices = []
    await registry.sync_transport(transport, None)

    refreshed = await registry.get_device(record.uid)
    assert refreshed.status == DeviceState.OFFLINE.value
    assert refreshed.uid == record.uid


@pytest.mark.asyncio
async def test_mark_status_records_the_error(session):
    registry = DeviceRegistry(session)
    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device")],
    )
    record = await registry.upsert_device(transport, transport._devices[0], None)

    updated = await registry.mark_status(
        record, DeviceState.PERMISSION_DENIED.value, "Permission denied: /data"
    )
    assert updated.status == DeviceState.PERMISSION_DENIED.value
    assert "Permission denied" in (updated.last_error or "")
