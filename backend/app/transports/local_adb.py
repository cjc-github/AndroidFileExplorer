"""Local host transport: talks to the ADB server of this very machine."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from app.logger import logger

from .base import (
    DeviceEntry,
    DeviceNotFoundError,
    DeviceOfflineError,
    FileEntry,
    PermissionDeniedError,
    TransferResult,
    Transport,
    TransportError,
    TransportTimeoutError,
    assert_safe_device_path,
    classify_output,
    directory_listing_path,
    normalize_device_path,
    parse_long_listing,
)

DEFAULT_SHELL_TIMEOUT = 15.0


class LocalAdbTransport(Transport):
    """ADB transport for devices attached to (or reachable from) this host."""

    kind = "local"

    def __init__(
        self,
        adb_path: str = "adb",
        server_host: str = "127.0.0.1",
        server_port: int = 5037,
    ) -> None:
        self.adb_path = adb_path
        self.server_host = server_host
        self.server_port = server_port
        resolved = shutil.which(adb_path) or adb_path
        self._adb = resolved

    @property
    def endpoint(self) -> str:
        return f"local:{self.server_host}:{self.server_port}"

    def _run(self, args: list[str], timeout: float = DEFAULT_SHELL_TIMEOUT) -> subprocess.CompletedProcess[str]:
        """Run an adb subcommand with an argument array (never a shell string)."""
        env = dict(os.environ)
        env["ADB_SERVER_SOCKET"] = f"tcp:{self.server_host}:{self.server_port}"
        try:
            return subprocess.run(
                [self._adb, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise TransportError(f"未找到 adb 命令: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TransportTimeoutError(f"adb 命令超时: {' '.join(args)}") from exc

    async def list_devices(self) -> list[DeviceEntry]:
        def _call() -> list[DeviceEntry]:
            result = self._run(["devices", "-l"], timeout=DEFAULT_SHELL_TIMEOUT)
            if result.returncode != 0:
                message = (result.stderr or result.stdout or "").strip()
                raise TransportError(f"获取设备列表失败: {message}")
            return parse_adb_devices(result.stdout)

        return await asyncio.to_thread(_call)

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        def _call() -> str:
            result = self._run(
                ["-s", serial, "shell", command],
                timeout=timeout or DEFAULT_SHELL_TIMEOUT,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                error = classify_output(output)
                if error is not None:
                    raise error
                raise TransportError(output.strip() or f"命令执行失败: {command}")
            return output

        return await asyncio.to_thread(_call)

    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        normalized = assert_safe_device_path(path)

        def _call() -> list[FileEntry]:
            result = self._run(
                ["-s", serial, "shell", "ls", "-la", directory_listing_path(normalized)],
                timeout=DEFAULT_SHELL_TIMEOUT,
            )
            output = (result.stdout or "") + (result.stderr or "")
            error = classify_output(output)
            if error is not None:
                raise error
            if result.returncode != 0:
                raise TransportError(output.strip() or f"获取文件列表失败: {normalized}")
            return parse_long_listing(result.stdout, normalized)

        return await asyncio.to_thread(_call)

    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        normalized = assert_safe_device_path(device_path)
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        def _call() -> TransferResult:
            result = self._run(
                ["-s", serial, "pull", normalized, str(target)],
                timeout=600,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                error = classify_output(output)
                if error is not None:
                    raise error
                raise TransportError(output.strip() or f"拉取文件失败: {normalized}")
            size = target.stat().st_size if target.exists() else None
            return TransferResult(
                ok=True,
                path=normalized,
                size=size,
                stored_at=str(target),
                message="拉取成功",
            )

        return await asyncio.to_thread(_call)

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        source = Path(local_path)
        if not source.is_file():
            raise TransportError(f"本地文件不存在: {local_path}")
        normalized = assert_safe_device_path(device_path)
        parent = normalized.rsplit("/", 1)[0] or "/"

        def _call() -> TransferResult:
            result = self._run(
                ["-s", serial, "push", str(source), normalized],
                timeout=600,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                error = classify_output(output)
                if error is not None:
                    raise error
                raise TransportError(output.strip() or f"上传文件失败: {normalized}")
            return TransferResult(
                ok=True,
                path=normalized,
                size=source.stat().st_size,
                stored_at=normalized,
                message="上传成功",
            )

        return await asyncio.to_thread(_call)

    async def adb_root(self, serial: str) -> str:
        def _call() -> str:
            result = self._run(["-s", serial, "root"], timeout=20)
            return (result.stdout or "") + (result.stderr or "")

        return await asyncio.to_thread(_call)

    async def adb_unroot(self, serial: str) -> str:
        def _call() -> str:
            result = self._run(["-s", serial, "unroot"], timeout=20)
            return (result.stdout or "") + (result.stderr or "")

        return await asyncio.to_thread(_call)

    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            result = self._run(["connect", serial], timeout=30)
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            lowered = output.lower()
            ok = "connected to" in lowered or "already connected" in lowered
            return ok, output

        return await asyncio.to_thread(_call)

    async def disconnect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            result = self._run(["disconnect", serial], timeout=30)
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            ok = "disconnected" in output.lower() or result.returncode == 0
            return ok, output

        return await asyncio.to_thread(_call)


def parse_adb_devices(output: str) -> list[DeviceEntry]:
    """Parse ``adb devices -l`` output into device entries."""
    entries: list[DeviceEntry] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial = fields[0]
        state = fields[1]
        props: dict[str, str] = {}
        for field in fields[2:]:
            key, _, value = field.partition(":")
            if key:
                props[key] = value
        entries.append(
            DeviceEntry(
                serial=serial,
                state=state,
                model=props.get("model"),
                brand=props.get("brand"),
                product=props.get("product"),
                device=props.get("device"),
                transport_id=props.get("transport_id"),
            )
        )
    return entries
