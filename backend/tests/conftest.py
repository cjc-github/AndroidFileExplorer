"""Shared pytest fixtures: in-memory DB and a fake transport."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app.models.device import DeviceRecord, EngineRecord  # noqa: E402
from app.services.registry import build_device_uid  # noqa: E402
from app.transports.base import (  # noqa: E402
    DeviceEntry,
    FileEntry,
    MetricsSample,
    PermissionDeniedError,
    StorageSample,
    TransferResult,
    Transport,
)


class FakeTransport(Transport):
    """A transport backed by an in-memory fake Android device."""

    kind = "fake"

    def __init__(
        self,
        endpoint: str = "fake:1",
        devices: list[DeviceEntry] | None = None,
        tree: dict[str, list[FileEntry]] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._devices = devices or []
        self._tree = tree or {}
        self.connected: set[str] = set()
        self.disconnected: set[str] = set()
        self.pushed: list[tuple[str, str]] = []
        self.pulled: list[tuple[str, str]] = []
        self.permission_denied_paths: set[str] = set()
        self.rooted: bool = False

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def list_devices(self) -> list[DeviceEntry]:
        return list(self._devices)

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        if "Permission denied" in command:
            raise PermissionDeniedError("Permission denied")
        if command.strip() == "id":
            return "uid=0(root) gid=0(root)" if self.rooted else "uid=2000(shell) gid=2000(shell)"
        return ""

    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        if path in self.permission_denied_paths:
            raise PermissionDeniedError(f"Permission denied: {path}")
        return list(self._tree.get(path, []))

    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        self.pulled.append((device_path, local_path))
        # The real transports leave the file on disk; the download route
        # relies on that, so the fake does the same.
        payload = f"fake content of {device_path}".encode()
        Path(local_path).write_bytes(payload)
        return TransferResult(ok=True, path=device_path, size=len(payload), stored_at=local_path)

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        self.pushed.append((local_path, device_path))
        return TransferResult(ok=True, path=device_path, size=10, stored_at=device_path)

    async def adb_root(self, serial: str) -> str:
        """The fake device can be escalated, like an emulator."""
        self.rooted = True
        return "restarting adbd as root"

    async def is_rooted(self, serial: str) -> bool:
        return self.rooted

    async def connect(self, serial: str) -> tuple[bool, str]:
        self.connected.add(serial)
        return True, f"connected to {serial}"

    async def disconnect(self, serial: str) -> tuple[bool, str]:
        self.disconnected.add(serial)
        return True, f"disconnected {serial}"

    async def metrics(self, serial: str) -> MetricsSample:
        sample = MetricsSample()
        sample.raw_total_jiffies = 100
        sample.raw_idle_jiffies = 60
        sample.memory_total_bytes = 1024 * 1024 * 1024
        sample.memory_available_bytes = 512 * 1024 * 1024
        sample.memory_used_bytes = 512 * 1024 * 1024
        sample.memory_percent = 50.0
        sample.cpu_current_freq_khz = 1_800_000
        sample.storage = (
            StorageSample(
                path="/data",
                total_bytes=100 * 1024 * 1024 * 1024,
                used_bytes=40 * 1024 * 1024 * 1024,
                available_bytes=60 * 1024 * 1024 * 1024,
            ),
            StorageSample(
                path="/sdcard",
                total_bytes=50 * 1024 * 1024 * 1024,
                used_bytes=10 * 1024 * 1024 * 1024,
                available_bytes=40 * 1024 * 1024 * 1024,
            ),
        )
        return sample


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def uid_factory():
    return build_device_uid


@pytest.fixture
def event_loop():
    """A fresh event loop per test; pytest-asyncio closes the previous one."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()
