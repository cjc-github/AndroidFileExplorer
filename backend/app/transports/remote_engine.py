"""Remote machine transport: reaches Android devices through an MDD engine.

The engine (``agent_server``) lives on another machine and exposes the ADB
server of that machine over the MDD TCP protocol. Every command is forwarded
as an ``adb ...`` command line, exactly like the reference implementation in
``apk_survey/fuzzing/device_client.py`` does.

The client keeps ONE persistent connection per machine: opening a TCP
connection and running ``adb devices`` over it costs seconds, and the
foreground polls every few seconds.
"""

from __future__ import annotations

import asyncio
import shlex
import threading
import time
from pathlib import Path

from app.logger import logger

from .base import (
    DeviceEntry,
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
from .local_adb import parse_adb_devices
from .mdd_client import MddClient, MddConnectionError, MddError, MddTimeoutError


class RemoteEngineTransport(Transport):
    """Transport for devices reachable through a remote MDD engine."""

    kind = "remote"

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 5.0,
        command_timeout: float = 30.0,
        token: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.token = token
        self._client: MddClient | None = None
        self._lock = threading.Lock()
        self._last_failure: str | None = None
        self._failed_until = 0.0
        """While this timestamp is in the future, skip the machine."""

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    # ------------------------------------------------------------------ helpers

    def _client_instance(self) -> MddClient:
        return MddClient(
            self.host,
            self.port,
            connect_timeout=self.connect_timeout,
            command_timeout=self.command_timeout,
            token=self.token,
        )

    def _execute(self, command: str, timeout: float | None = None) -> str:
        """Run one command on the engine, reusing the persistent connection."""
        with self._lock:
            # A machine that just failed is skipped for a short while, so the
            # foreground is not blocked by a dead machine on every poll.
            if time.monotonic() < self._failed_until:
                raise TransportError(
                    self._last_failure or f"{self.endpoint} 暂时不可用"
                )

            client = self._client
            if client is None:
                client = self._client_instance()
                try:
                    client.connect()
                except MddConnectionError as exc:
                    self._mark_failed(str(exc))
                    raise TransportError(str(exc)) from exc
                self._client = client

            try:
                result = client.execute_command(command, timeout=timeout)
            except (MddConnectionError, MddTimeoutError) as exc:
                # The connection is broken: drop it, the next call reconnects.
                self._drop_client()
                self._mark_failed(str(exc))
                raise TransportError(str(exc)) from exc
            except MddError as exc:
                self._drop_client()
                raise TransportError(str(exc)) from exc

        output = result.message or ""
        error = classify_output(output)
        if error is not None:
            raise error
        if not result.success:
            raise TransportError(result.error or output or "引擎命令执行失败")
        return output

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def _mark_failed(self, message: str) -> None:
        self._last_failure = message
        self._failed_until = time.monotonic() + min(self.connect_timeout, 5.0)
        logger.warning(f"[remote {self.endpoint}] temporarily unavailable: {message}")

    def _reset_failure(self) -> None:
        self._last_failure = None
        self._failed_until = 0.0

    # ------------------------------------------------------------------- devices

    async def list_devices(self) -> list[DeviceEntry]:
        def _call() -> list[DeviceEntry]:
            try:
                output = self._execute("adb devices -l", timeout=self.command_timeout)
            except TransportError as exc:
                logger.warning(f"[remote {self.endpoint}] list_devices failed: {exc}")
                return []
            self._reset_failure()
            entries = parse_adb_devices(output)
            for entry in entries:
                if entry.state == "device":
                    entry.hardware_id = self._hardware_id(entry.serial)
            return entries

        return await asyncio.to_thread(_call)

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        def _call() -> str:
            return self._execute(
                f"adb -s {shlex.quote(serial)} shell {command}",
                timeout=timeout or self.command_timeout,
            )

        return await asyncio.to_thread(_call)

    def _hardware_id(self, serial: str) -> str | None:
        """Read the device's own serial number (blocking, inside the thread)."""
        for prop in ("ro.serialno", "ro.boot.serialno"):
            try:
                value = self._execute(
                    f"adb -s {shlex.quote(serial)} shell getprop {prop}", timeout=10
                ).strip()
            except TransportError:
                continue
            if value and "not found" not in value.lower() and "unknown" not in value.lower():
                return value
        return None

    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        normalized = assert_safe_device_path(path)

        def _call() -> list[FileEntry]:
            output = self._execute(
                f"adb -s {shlex.quote(serial)} shell ls -la "
                f"{shlex.quote(directory_listing_path(normalized))}",
                timeout=self.command_timeout,
            )
            return parse_long_listing(output, normalized)

        return await asyncio.to_thread(_call)

    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        normalized = assert_safe_device_path(device_path)

        def _call() -> TransferResult:
            # The engine pulls onto ITS OWN filesystem; the API server only gets
            # the report. Be explicit about it instead of pretending the file
            # landed locally.
            client = self._client_instance()
            try:
                client.connect()
                result = client.pull_file(
                    normalized,
                    local_path,
                    device_id=serial,
                    timeout=max(self.command_timeout, 600),
                )
            except MddConnectionError as exc:
                raise TransportError(str(exc)) from exc
            except MddTimeoutError as exc:
                raise TransportTimeoutError(str(exc)) from exc
            except MddError as exc:
                raise TransportError(str(exc)) from exc
            finally:
                client.close()

            if not result.success:
                error = classify_output(result.error or result.message)
                if error is not None:
                    raise error
                raise TransportError(result.error or result.message or "拉取文件失败")

            return TransferResult(
                ok=True,
                path=normalized,
                size=None,
                stored_at=f"{self.endpoint}:{local_path}",
                message=result.message or "拉取成功",
            )

        return await asyncio.to_thread(_call)

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        source = Path(local_path)
        if not source.is_file():
            raise TransportError(f"本地文件不存在: {local_path}")
        normalized = assert_safe_device_path(device_path)

        def _call() -> TransferResult:
            client = self._client_instance()
            try:
                client.connect()
                result = client.push_file(
                    str(source),
                    normalized,
                    device_id=serial,
                    timeout=max(self.command_timeout, 600),
                )
            except MddConnectionError as exc:
                raise TransportError(str(exc)) from exc
            except MddTimeoutError as exc:
                raise TransportTimeoutError(str(exc)) from exc
            except MddError as exc:
                raise TransportError(str(exc)) from exc
            finally:
                client.close()

            if not result.success:
                error = classify_output(result.error or result.message)
                if error is not None:
                    raise error
                raise TransportError(result.error or result.message or "上传文件失败")

            return TransferResult(
                ok=True,
                path=normalized,
                size=source.stat().st_size,
                stored_at=f"{self.endpoint}:{normalized}",
                message=result.message or "上传成功",
            )

        return await asyncio.to_thread(_call)

    async def adb_root(self, serial: str) -> str:
        def _call() -> str:
            return self._execute(f"adb -s {shlex.quote(serial)} root", timeout=20)

        return await asyncio.to_thread(_call)

    async def adb_unroot(self, serial: str) -> str:
        def _call() -> str:
            return self._execute(f"adb -s {shlex.quote(serial)} unroot", timeout=20)

        return await asyncio.to_thread(_call)

    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            try:
                output = self._execute(f"adb connect {shlex.quote(serial)}", timeout=30)
            except TransportError as exc:
                return False, str(exc)
            lowered = output.lower()
            ok = "connected to" in lowered or "already connected" in lowered
            return ok, output

        return await asyncio.to_thread(_call)

    async def disconnect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            try:
                output = self._execute(f"adb disconnect {shlex.quote(serial)}", timeout=30)
            except TransportError as exc:
                return False, str(exc)
            ok = "disconnected" in output.lower() or "error" not in output.lower()
            return ok, output

        return await asyncio.to_thread(_call)

    async def close(self) -> None:
        with self._lock:
            self._drop_client()
