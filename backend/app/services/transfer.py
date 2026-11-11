"""Pull / push orchestration between the API server and the devices."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from app.config import settings
from app.logger import logger
from app.transports.base import (
    PermissionDeniedError,
    TransferResult,
    Transport,
    TransportError,
    normalize_device_path,
)


def _unique_staging_name(name: str) -> str:
    """Add a short random token so concurrent transfers never collide."""
    path = Path(name)
    stem = path.stem or "file"
    suffix = path.suffix
    return f"{stem}_{uuid.uuid4().hex[:12]}{suffix}"


class TransferService:
    """Moves files between the API server and the devices."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.upload_dir = Path(settings.upload_dir)
        self.download_dir = Path(settings.download_dir)

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._sweep()

    def _sweep(self) -> None:
        """Drop staged files older than the retention window.

        The staging directories used to grow forever: every download left a
        copy behind and nothing ever cleaned it up.  Each transfer
        opportunistically removes files older than ``STAGING_MAX_AGE_HOURS``
        (0 disables the sweep).  In-flight transfers are safe: freshly
        written files are never older than the window.
        """
        max_age_seconds = settings.staging_max_age_hours * 3600
        if max_age_seconds <= 0:
            return
        deadline = time.time() - max_age_seconds
        for directory in (self.upload_dir, self.download_dir):
            try:
                for entry in directory.iterdir():
                    try:
                        if entry.is_file() and entry.stat().st_mtime < deadline:
                            entry.unlink()
                    except OSError:
                        continue
            except OSError:
                continue

    async def pull(self, serial: str, device_path: str) -> TransferResult:
        """Download a device file into the server side download directory."""
        normalized = normalize_device_path(device_path)
        self.ensure_dirs()
        name = normalized.rsplit("/", 1)[-1] or "download.bin"
        target = self.download_dir / _unique_staging_name(name)

        result = await self.transport.pull(serial, normalized, str(target))
        # The remote engine labels stored_at with the endpoint, but the
        # payload was streamed into the staging directory: report the real
        # local path so the caller (download endpoint) can serve the file.
        if target.exists():
            result.stored_at = str(target)
        logger.info(f"pull {normalized} -> {target} ok={result.ok}")
        return result

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        """Upload a local file onto a device."""
        source = Path(local_path)
        if not source.is_file():
            raise TransportError(f"本地文件不存在: {local_path}")
        size = source.stat().st_size
        if size > settings.max_upload_bytes:
            raise TransportError(
                f"文件超过上传上限: {size} > {settings.max_upload_bytes}"
            )

        normalized = normalize_device_path(device_path)
        result = await self.transport.push(serial, str(source), normalized)
        logger.info(f"push {source} -> {normalized} ok={result.ok}")
        return result

    def stage_upload(self, filename: str, content: bytes) -> Path:
        """Store an uploaded file in the staging directory and return its path."""
        self.ensure_dirs()
        safe_name = Path(filename).name or "upload.bin"
        target = self.upload_dir / _unique_staging_name(safe_name)
        target.write_bytes(content)
        return target

    async def stage_upload_stream(
        self, filename: str, fileobj, max_bytes: int | None = None
    ) -> Path:
        """Stream a multipart upload to the staging directory.

        The body is written in chunks instead of being buffered whole in
        memory (the configured cap is 2 GiB - buffering that would OOM the
        server), and the limit aborts the write as soon as it is exceeded.
        A partially written file is removed before the error propagates.
        """
        self.ensure_dirs()
        safe_name = Path(filename).name or "upload.bin"
        target = self.upload_dir / _unique_staging_name(safe_name)
        limit = settings.max_upload_bytes if max_bytes is None else max_bytes

        def _copy() -> None:
            received = 0
            try:
                with open(target, "wb") as out:
                    while True:
                        chunk = fileobj.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if limit is not None and received > limit:
                            raise ValueError(
                                f"文件超过上传上限: {received} > {limit}"
                            )
                        out.write(chunk)
            except BaseException:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        await asyncio.to_thread(_copy)
        return target
