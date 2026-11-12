"""HTTP API integration tests.

The transport is replaced by the fake one so the whole API surface can be
exercised without a real Android device.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings
from app.db import session as db_session
from app.main import app
from app.services.registry import build_device_uid
from app.state import get_metrics_collector, get_transport_resolver
from app.transports.base import DeviceEntry, FileEntry, TransferResult

from .conftest import FakeTransport

TREE: dict[str, list[FileEntry]] = {
    "/": [
        FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096),
        FileEntry(name="system", path="/system", is_dir=True, size=4096),
    ],
    "/sdcard": [
        FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=12),
        FileEntry(name="lib", path="/sdcard/lib", is_dir=True, size=4096),
    ],
    "/sdcard/lib": [
        FileEntry(name="libtest.so", path="/sdcard/lib/libtest.so", is_dir=False, size=64),
    ],
}


def run_sync(coro):
    """Run a coroutine from synchronous test code (own event loop)."""
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose transport and database are fakes."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # Staging directories live in tmp as well, so transfers of the tests
    # never touch the real backend/data folder.
    monkeypatch.setattr(settings, "download_dir", tmp_path / "downloads")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "staging_max_age_hours", 0)

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


def _seed_device(client) -> str:
    """Create one online device in the registry and return its uid."""
    transport = client.transport
    uid = build_device_uid(transport.kind, transport.endpoint, "emulator-5554")

    async def _run() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            registry = DeviceRegistry(session)
            await registry.upsert_device(transport, transport._devices[0], None)

    run_sync(_run())
    return uid


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_device_list_refresh_discovers_devices(client):
    response = client.get("/api/v1/devices", params={"refresh": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert len(body["data"]) == 1
    device = body["data"][0]
    assert device["serial"] == "emulator-5554"
    assert device["status"] == "online"
    assert device["uid"]


def test_device_list_paginates_and_filters(client):
    """The paged endpoint returns one page, the global total and a filter."""
    transport = client.transport
    for serial, model in (
        ("device-alpha", "Alpha"),
        ("device-beta", "Beta"),
        ("device-gamma", "Gamma"),
    ):
        transport._devices.append(
            DeviceEntry(serial=serial, state="device", model=model)
        )

    async def _seed_all() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            registry = DeviceRegistry(session)
            for entry in transport._devices:
                await registry.upsert_device(transport, entry, None)

    run_sync(_seed_all())

    first = client.get(
        "/api/v1/devices", params={"page": 1, "page_size": 2}
    ).json()["data"]
    assert first["total"] == 4
    assert first["page"] == 1
    assert first["page_size"] == 2
    assert len(first["items"]) == 2

    second = client.get(
        "/api/v1/devices", params={"page": 2, "page_size": 2}
    ).json()["data"]
    assert second["total"] == 4
    assert len(second["items"]) == 2
    assert {item["uid"] for item in first["items"]}.isdisjoint(
        {item["uid"] for item in second["items"]}
    )

    sorted_page = client.get(
        "/api/v1/devices",
        params={
            "page": 1,
            "page_size": 10,
            "sort_by": "device",
            "sort_order": "asc",
        },
    ).json()["data"]
    names = [
        item["alias"] or item["model"] or item["serial"]
        for item in sorted_page["items"]
    ]
    assert names == sorted(names, key=str.lower)

    filtered = client.get(
        "/api/v1/devices",
        params={"page": 1, "page_size": 20, "keyword": "beta"},
    ).json()["data"]
    assert filtered["total"] == 1
    assert filtered["items"][0]["serial"] == "device-beta"


def test_device_list_page_only_samples_the_current_page(client, monkeypatch):
    """Pagination exists to keep polling cheap: other pages are not sampled."""
    transport = client.transport
    for serial in ("page-a", "page-b", "page-c"):
        transport._devices.append(
            DeviceEntry(serial=serial, state="device", model=serial)
        )

    async def _seed_all() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            registry = DeviceRegistry(session)
            for entry in transport._devices:
                await registry.upsert_device(transport, entry, None)

    run_sync(_seed_all())

    collector = get_metrics_collector()
    for entry in transport._devices:
        collector.reset(
            build_device_uid(transport.kind, transport.endpoint, entry.serial)
        )

    sampled: list[str] = []

    async def _counting_metrics(serial):
        sampled.append(serial)
        return await FakeTransport.metrics(transport, serial)

    monkeypatch.setattr(transport, "metrics", _counting_metrics)

    body = client.get(
        "/api/v1/devices", params={"page": 1, "page_size": 2}
    ).json()["data"]
    assert len(body["items"]) == 2
    assert len(sampled) == 2
    assert set(sampled) == {item["serial"] for item in body["items"]}


def test_device_list_rejects_invalid_page_size(client):
    response = client.get("/api/v1/devices", params={"page": 1, "page_size": 0})
    assert response.status_code == 422


def test_device_detail_carries_metrics(client):
    uid = _seed_device(client)
    response = client.get(f"/api/v1/devices/{uid}")
    assert response.status_code == 200
    device = response.json()["data"]
    assert device["uid"] == uid
    assert device["metrics"]["memory_percent"] == 50.0


def test_metrics_endpoint_returns_cpu_and_memory(client):
    uid = _seed_device(client)
    response = client.get(f"/api/v1/devices/{uid}/metrics")
    assert response.status_code == 200
    metrics = response.json()["data"]
    assert metrics["memory_total_bytes"] == 1024 * 1024 * 1024
    assert metrics["memory_percent"] == 50.0
    assert metrics["cpu_current_freq_khz"] == 1_800_000
    assert [item["path"] for item in metrics["storage"]] == ["/data", "/sdcard"]
    assert metrics["storage"][0]["total_bytes"] == 100 * 1024 * 1024 * 1024
    assert "cpu_percent" in metrics


def test_device_list_samples_metrics_concurrently(client, monkeypatch):
    """Two devices on one machine are sampled at the same time."""
    transport = client.transport
    transport._devices.append(
        DeviceEntry(serial="emulator-5556", state="device", model="Pixel_6")
    )
    _seed_device(client)

    async def _seed_second() -> None:
        from app.services.registry import DeviceRegistry

        async with db_session.async_session_maker() as session:
            await DeviceRegistry(session).upsert_device(
                transport, transport._devices[1], None
            )

    run_sync(_seed_second())

    # The collector caches samples per uid (same uids across tests), so the
    # earlier tests would satisfy the second device from the cache.
    collector = get_metrics_collector()
    collector.reset(uid1 := build_device_uid(transport.kind, transport.endpoint, "emulator-5554"))
    collector.reset(uid2 := build_device_uid(transport.kind, transport.endpoint, "emulator-5556"))

    active = 0
    peak = 0

    async def _slow_metrics(serial):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
            return await FakeTransport.metrics(transport, serial)
        finally:
            active -= 1

    monkeypatch.setattr(transport, "metrics", _slow_metrics)

    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 2
    assert peak >= 2, "the devices must be sampled in parallel, not one by one"


def test_unknown_device_is_404(client):
    response = client.get("/api/v1/devices/does-not-exist")
    assert response.status_code == 404


def test_file_listing_returns_directory_structure(client):
    uid = _seed_device(client)
    response = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/sdcard"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["current_path"] == "/sdcard"
    assert {item["name"] for item in data["items"]} == {"hello.txt", "lib"}
    assert data["parts"] == [{"name": "sdcard", "path": "/sdcard"}]


def test_permission_denied_path_returns_403(client):
    uid = _seed_device(client)
    response = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/system"})
    assert response.status_code == 403
    assert "Permission denied" in response.json()["detail"]


def test_file_search_filters_by_keyword(client):
    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/search",
        params={"keyword": "libtest", "path": "/"},
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["name"] for item in items] == ["libtest.so"]


def test_pull_and_push_round_trip(client, tmp_path):
    uid = _seed_device(client)
    transport = client.transport

    response = client.post(
        f"/api/v1/devices/{uid}/files/pull", json={"path": "/sdcard/hello.txt"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True
    assert transport.pulled, "the transport must have been asked to pull"

    source = tmp_path / "upload.bin"
    source.write_bytes(b"upload me")

    response = client.post(
        f"/api/v1/devices/{uid}/files/push",
        json={"path": "/sdcard/uploaded.bin", "local_path": str(source)},
    )
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True
    assert transport.pushed[-1][1] == "/sdcard/uploaded.bin"


def test_download_streams_the_device_file(client):
    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/download", params={"path": "/sdcard/hello.txt"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert "hello.txt" in response.headers["content-disposition"]
    assert b"fake content of /sdcard/hello.txt" in response.content


def test_download_404s_when_the_staging_file_never_landed(client, monkeypatch):
    """A transport that reports success without leaving the file is a miss."""
    uid = _seed_device(client)

    async def _pull_without_file(self, serial, device_path, local_path):
        self.pulled.append((device_path, local_path))
        return TransferResult(ok=True, path=device_path, size=0, stored_at=local_path)

    monkeypatch.setattr(FakeTransport, "pull", _pull_without_file)
    response = client.get(
        f"/api/v1/devices/{uid}/files/download", params={"path": "/sdcard/hello.txt"}
    )
    assert response.status_code == 404
    assert "文件未落在" in response.json()["detail"]


def test_connect_and_disconnect_are_lifecycle_operations(client):
    uid = _seed_device(client)
    transport = client.transport

    response = client.post(f"/api/v1/devices/{uid}/connect")
    assert response.status_code == 200
    assert "emulator-5554" in transport.connected

    response = client.post(f"/api/v1/devices/{uid}/disconnect")
    assert response.status_code == 200
    assert "emulator-5554" in transport.disconnected
    assert response.json()["data"]["status"] == "offline"


def test_engine_list_contains_the_local_machine(client):
    response = client.get("/api/v1/engines")
    assert response.status_code == 200
    engines = response.json()["data"]
    assert any(engine["kind"] == "local" for engine in engines)

def test_file_preview_returns_text(client):
    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/preview", params={"path": "/sdcard/hello.txt"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kind"] == "text"
    assert "fake content" in data["content"]


def test_file_preview_rejects_directories(client):
    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/preview", params={"path": "/sdcard/lib"}
    )
    assert response.status_code == 400
    assert "目录" in response.json()["detail"]


def test_directory_archive_contains_nested_files(client):
    import io
    import zipfile

    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/archive", params={"path": "/sdcard"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert "hello.txt" in names
    assert "lib/libtest.so" in names


def test_file_search_honours_the_result_limit(client):
    uid = _seed_device(client)
    response = client.get(
        f"/api/v1/devices/{uid}/files/search",
        params={"keyword": ".", "path": "/sdcard", "limit": 1},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["items"]) == 1
    assert data["truncated"] is True


def test_selection_archive_zips_files_and_directories(client):
    import io
    import zipfile

    uid = _seed_device(client)
    response = client.post(
        f"/api/v1/devices/{uid}/files/archive-selection",
        json={
            "paths": [
                "/sdcard/hello.txt",
                "/sdcard/lib",
                # Overlaps the directory above: must not appear twice.
                "/sdcard/lib/libtest.so",
            ]
        },
    )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
    assert "sdcard/hello.txt" in names
    assert "sdcard/lib/libtest.so" in names
    assert len(names) == len(set(names))
