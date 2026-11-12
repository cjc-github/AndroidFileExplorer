"""Tests for the machine (engine) management endpoints.

These back the "远程机器连接配置" page: list, create, probe, delete.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.db import session as db_session
from app.main import app
from app.state import get_transport_resolver

from .conftest import FakeTransport


def run_sync(coro):
    """Run a coroutine from synchronous test code (own event loop)."""
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[],
        tree={},
    )

    resolver = get_transport_resolver()
    original_local = resolver._local
    original_override = resolver._remote_override
    resolver._local = transport  # type: ignore[assignment]
    # Remote engines must resolve to a fake transport as well, otherwise the
    # test would probe a real machine on the network.
    resolver._remote_override = lambda _host, _port: transport

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
    resolver._remote_override = original_override
    run_sync(engine.dispose())


def test_local_machine_is_registered_on_startup(client):
    response = client.get("/api/v1/engines")
    assert response.status_code == 200
    engines = response.json()["data"]
    assert len(engines) == 1
    assert engines[0]["kind"] == "local"
    assert engines[0]["host"] == "127.0.0.1"
    assert engines[0]["device_count"] == 0


def test_create_remote_machine(client):
    response = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    engine = body["data"]
    assert engine["host"] == "192.168.114.130"
    assert engine["kind"] == "remote"

    listing = client.get("/api/v1/engines").json()["data"]
    assert len(listing) == 2
    assert {item["host"] for item in listing} == {"127.0.0.1", "192.168.114.130"}


def test_probe_machine_reports_device_count(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]

    response = client.post(f"/api/v1/engines/{engine['id']}/test")
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["engine_id"] == engine["id"]
    assert result["status"] == "connected"
    # The fake transport reports no devices; the point is that the probe
    # used the fake and not a real machine on the network.
    assert result["device_count"] == 0


def test_probe_unknown_machine_is_404(client):
    response = client.post("/api/v1/engines/999999/test")
    assert response.status_code == 404


def test_delete_remote_machine(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]

    response = client.delete(f"/api/v1/engines/{engine['id']}")
    assert response.status_code == 200

    listing = client.get("/api/v1/engines").json()["data"]
    assert len(listing) == 1
    assert listing[0]["kind"] == "local"


def test_local_machine_cannot_be_deleted(client):
    engines = client.get("/api/v1/engines").json()["data"]
    local = next(item for item in engines if item["kind"] == "local")

    response = client.delete(f"/api/v1/engines/{local['id']}")
    assert response.status_code == 400
    assert "本机" in response.json()["detail"]


def test_delete_unknown_machine_is_404(client):
    response = client.delete("/api/v1/engines/999999")
    assert response.status_code == 404


def test_machine_payload_validation(client):
    response = client.post(
        "/api/v1/engines", json={"name": "x", "host": "", "port": 12345, "kind": "remote"}
    )
    assert response.status_code == 422


def test_registering_the_same_machine_twice_is_rejected(client):
    """One machine must not end up with two registry rows / transports."""
    payload = {"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"}
    assert client.post("/api/v1/engines", json=payload).status_code == 200

    response = client.post("/api/v1/engines", json=payload)
    assert response.status_code == 400
    assert "已登记" in response.json()["detail"]


def test_update_machine_renames_and_rotates_token(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]

    response = client.patch(
        f"/api/v1/engines/{engine['id']}",
        json={"name": "新名字", "token": "rotated-secret"},
    )
    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["name"] == "新名字"
    assert updated["host"] == "192.168.114.130"

    listing = client.get("/api/v1/engines").json()["data"]
    assert any(item["name"] == "新名字" for item in listing)


def test_update_machine_rejects_duplicate_address(client):
    first = client.post(
        "/api/v1/engines",
        json={"name": "a", "host": "192.168.1.10", "port": 12345, "kind": "remote"},
    ).json()["data"]
    client.post(
        "/api/v1/engines",
        json={"name": "b", "host": "192.168.1.11", "port": 12345, "kind": "remote"},
    )

    response = client.patch(
        f"/api/v1/engines/{first['id']}",
        json={"host": "192.168.1.11"},
    )
    assert response.status_code == 400
    assert "已登记" in response.json()["detail"]


def test_delete_machine_drops_the_resolver_entry(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]
    resolver = get_transport_resolver()
    assert engine["id"] in resolver._engines

    assert client.delete(f"/api/v1/engines/{engine['id']}").status_code == 200
    assert engine["id"] not in resolver._engines


def test_engine_info_reports_stored_ssh_password_without_echoing_it():
    from app.models.device import EngineRecord
    from app.schemas.device import EngineInfo

    record = EngineRecord(
        id=1,
        name="ssh-box",
        host="192.168.1.20",
        port=2222,
        kind="ssh",
        ssh_username="root",
        ssh_password="top-secret",
        ssh_port=2222,
        status="connected",
    )
    info = EngineInfo.model_validate(record)
    assert info.has_ssh_password is True
    assert "top-secret" not in info.model_dump_json()
