"""Tests for the transfer staging behaviour: unique names, retention, streaming."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from app.config import settings
from app.services.transfer import TransferService

from .conftest import FakeTransport


def _service(tmp_path: Path) -> TransferService:
    service = TransferService(FakeTransport())
    service.upload_dir = tmp_path / "uploads"
    service.download_dir = tmp_path / "downloads"
    return service


def test_pull_lands_under_a_unique_name(tmp_path, monkeypatch):
    """Two pulls of the same file must not overwrite each other."""
    monkeypatch.setattr(settings, "staging_max_age_hours", 0)
    service = _service(tmp_path)

    asyncio.run(service.pull("emulator-5554", "/sdcard/hello.txt"))
    asyncio.run(service.pull("emulator-5554", "/sdcard/hello.txt"))

    targets = [local for _device, local in service.transport.pulled]
    assert len(targets) == 2
    assert targets[0] != targets[1]
    assert all(target.startswith(str(service.download_dir)) for target in targets)


def test_sweep_removes_expired_staging_files(tmp_path, monkeypatch):
    """Files older than the retention window are removed on the next setup."""
    monkeypatch.setattr(settings, "staging_max_age_hours", 24)
    service = _service(tmp_path)
    service.ensure_dirs()

    old = service.download_dir / "old.bin"
    old.write_bytes(b"x")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    fresh = service.upload_dir / "fresh.bin"
    fresh.write_bytes(b"x")

    service.ensure_dirs()

    assert not old.exists()
    assert fresh.exists()


def test_sweep_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "staging_max_age_hours", 0)
    service = _service(tmp_path)
    service.ensure_dirs()

    ancient = service.download_dir / "ancient.bin"
    ancient.write_bytes(b"x")
    very_old = time.time() - 365 * 24 * 3600
    os.utime(ancient, (very_old, very_old))

    service.ensure_dirs()

    assert ancient.exists()


def test_stage_upload_stream_enforces_the_size_limit(tmp_path, monkeypatch):
    """The copy aborts as soon as the limit is exceeded, partial file removed."""
    monkeypatch.setattr(settings, "staging_max_age_hours", 0)
    service = _service(tmp_path)

    class _Blob:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def read(self, _size=-1):
            return self._chunks.pop(0) if self._chunks else None

    with pytest.raises(ValueError):
        asyncio.run(
            service.stage_upload_stream(
                "big.bin", _Blob([b"x" * 1024] * 3), max_bytes=2048
            )
        )
    assert list((tmp_path / "uploads").iterdir()) == []

    target = asyncio.run(
        service.stage_upload_stream("ok.bin", _Blob([b"abc", b"def"]), max_bytes=100)
    )
    assert target.read_bytes() == b"abcdef"


def test_stage_upload_gets_a_unique_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "staging_max_age_hours", 0)
    service = _service(tmp_path)
    first = service.stage_upload("report.bin", b"one")
    second = service.stage_upload("report.bin", b"two")
    assert first != second
    assert first.name.startswith("report_") and first.suffix == ".bin"
