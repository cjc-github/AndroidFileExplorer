"""Remote machine transport: reaches Android devices through an MDD engine.

The engine (``agent_server``) lives on another machine and exposes the ADB
server of that machine over the MDD TCP protocol. Every command is forwarded
as an ``adb ...`` command line, exactly like the reference implementation in
``apk_survey/fuzzing/device_client.py`` does.

The client keeps a small POOL of persistent connections per machine: opening
a TCP connection and running ``adb devices`` over it costs seconds, the
foreground polls every few seconds, and commands for DIFFERENT devices must
not queue behind each other on one socket. The file transfers (push/pull)
use their own short-lived connections so a long transfer never starves the
command pool.
"""

from __future__ import annotations

import asyncio
import queue
import shlex
import threading
import time
from pathlib import Path

from app.config import settings
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
        pool_size: int | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.token = token
        self._pool_size = max(1, pool_size or settings.remote_pool_size)
        self._pool: queue.Queue[MddClient] = queue.Queue()
        self._pool_lock = threading.Lock()
        self._opened = 0
        """Live connections (in the pool AND checked out right now)."""
        self._closed = False
        self._pool_wait_timeout = 30.0
        """How long a caller waits when every pooled connection is busy."""
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

    def _acquire_client(self) -> MddClient:
        """Take a pooled connection, or open a new one while below the cap."""
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._pool_lock:
            open_new = not self._closed and self._opened < self._pool_size
            if open_new:
                self._opened += 1
        if open_new:
            client = self._client_instance()
            try:
                client.connect()
            except MddConnectionError as exc:
                with self._pool_lock:
                    self._opened -= 1
                self._mark_failed(str(exc))
                raise TransportError(str(exc)) from exc
            return client
        # Every slot is busy: wait for one to come back instead of opening
        # an unbounded number of connections.
        try:
            return self._pool.get(timeout=self._pool_wait_timeout)
        except queue.Empty as exc:
            raise TransportError(f"{self.endpoint} 命令连接池已满") from exc

    def _release_client(self, client: MddClient) -> None:
        """Hand a healthy connection back to the pool."""
        with self._pool_lock:
            if self._closed:
                shutdown = True
            else:
                self._pool.put(client)
                shutdown = False
        if shutdown:
            self._shutdown_client(client)

    def _discard_client(self, client: MddClient) -> None:
        """Drop a broken connection and shrink the live count."""
        self._shutdown_client(client)
        with self._pool_lock:
            self._opened -= 1

    @staticmethod
    def _shutdown_client(client: MddClient) -> None:
        try:
            client.close()
        except Exception:  # pragma: no cover - defensive
            pass

    def _execute(self, command: str, timeout: float | None = None) -> str:
        """Run one command on the engine over a pooled connection."""
        # A machine that just failed is skipped for a short while, so the
        # foreground is not blocked by a dead machine on every poll.
        if time.monotonic() < self._failed_until:
            raise TransportError(
                self._last_failure or f"{self.endpoint} 暂时不可用"
            )

        client = self._acquire_client()
        try:
            result = client.execute_command(command, timeout=timeout)
        except MddTimeoutError as exc:
            # The engine accepted the connection but did not answer: the
            # machine is hung.  Condemn it for a moment so the next poll
            # fails fast instead of waiting out the full timeout again.
            self._discard_client(client)
            self._mark_failed(str(exc))
            raise TransportError(str(exc)) from exc
        except MddConnectionError as exc:
            # One pooled connection died.  The machine may well be fine -
            # the other connections keep working - so only this connection
            # is dropped; condemning the whole machine would take every
            # device on it offline for the skip window.
            self._discard_client(client)
            raise TransportError(str(exc)) from exc
        except MddError as exc:
            self._discard_client(client)
            raise TransportError(str(exc)) from exc
        except Exception:
            # An unexpected error must not leak the slot: without this the
            # connection is neither pooled nor closed, so the pool shrinks by
            # one on every occurrence until it is permanently exhausted.
            self._discard_client(client)
            raise
        else:
            self._release_client(client)

        output = result.message or ""
        error = classify_output(output)
        if error is not None:
            raise error
        if not result.success:
            raise TransportError(result.error or output or "引擎命令执行失败")
        return output

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
        """Close every pooled connection."""
        with self._pool_lock:
            self._closed = True
        while True:
            try:
                client = self._pool.get_nowait()
            except queue.Empty:
                break
            self._shutdown_client(client)
