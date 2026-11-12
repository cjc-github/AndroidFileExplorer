"""Regression tests for the file operations (mkdir / rename / remove).

The operations are guarded: the root directory can never be created,
renamed or deleted, and a non existing path answers 404.
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


class RecordingTransport(FakeTransport):
    """Records the commands that were sent to the device."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.commands: list[str] = []

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        self.commands.append(command)
        return await super().shell(serial, command, timeout)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    transport = RecordingTransport(
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


def test_mkdir_creates_the_directory(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/mkdir", json={"path": "/sdcard/Download/new"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["path"] == "/sdcard/Download/new"
    assert any("mkdir" in command for command in client.transport.commands)


def test_rename_moves_the_entry(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/rename",
        json={"source": "/sdcard/a.txt", "target": "/sdcard/b.txt"},
    )
    assert response.status_code == 200
    assert any("mv" in command for command in client.transport.commands)


def test_remove_deletes_the_entry(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/remove",
        json={"path": "/sdcard/a.txt", "recursive": False},
    )
    assert response.status_code == 200
    assert any("rm " in command for command in client.transport.commands)


def test_the_root_directory_cannot_be_removed(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/remove", json={"path": "/", "recursive": True}
    )
    assert response.status_code == 400
    assert "根目录" in response.json()["detail"]


def test_the_root_directory_cannot_be_renamed(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/rename",
        json={"source": "/", "target": "/sdcard"},
    )
    assert response.status_code == 400


def test_the_root_directory_cannot_be_created(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/mkdir", json={"path": "/"}
    )
    assert response.status_code == 400


def test_the_alias_can_be_set(client):
    uid = _seed(client)
    response = client.patch(f"/api/v1/devices/{uid}", json={"alias": "测试机"})
    assert response.status_code == 200
    assert response.json()["data"]["alias"] == "测试机"

    detail = client.get(f"/api/v1/devices/{uid}").json()["data"]
    assert detail["alias"] == "测试机"


def test_an_empty_alias_clears_the_name(client):
    uid = _seed(client)
    client.patch(f"/api/v1/devices/{uid}", json={"alias": "测试机"})
    response = client.patch(f"/api/v1/devices/{uid}", json={"alias": "  "})
    assert response.status_code == 200
    assert response.json()["data"]["alias"] is None


def test_copy_uses_recursive_cp(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/copy",
        json={"source": "/sdcard/a", "target": "/sdcard/b"},
    )
    assert response.status_code == 200
    assert any("cp -r" in command for command in client.transport.commands)


def test_copy_rejects_the_root_directory(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/copy",
        json={"source": "/", "target": "/sdcard/b"},
    )
    assert response.status_code == 400


def test_permissions_use_chmod_and_chown(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/permissions",
        json={"path": "/sdcard/a", "mode": "644", "owner": "shell", "group": "shell"},
    )
    assert response.status_code == 200
    combined = " ".join(client.transport.commands)
    assert "chmod 644" in combined
    assert "chown shell:shell" in combined


def test_permissions_reject_a_non_octal_mode(client):
    uid = _seed(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/permissions",
        json={"path": "/sdcard/a", "mode": "rwx"},
    )
    assert response.status_code == 400
