"""Pull / push orchestration between the API server and the devices."""

from __future__ import annotations

import shutil
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


class TransferService:
    """Moves files between the API server and the devices."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.upload_dir = Path(settings.upload_dir)
        self.download_dir = Path(settings.download_dir)

    def ensure_dirs(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def pull(self, serial: str, device_path: str) -> TransferResult:
        """Download a device file into the server side download directory."""
        normalized = normalize_device_path(device_path)
        self.ensure_dirs()
        name = normalized.rsplit("/", 1)[-1] or "download.bin"
        target = self.download_dir / name

        result = await self.transport.pull(serial, normalized, str(target))
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
        target = self.upload_dir / safe_name
        target.write_bytes(content)
        return target
