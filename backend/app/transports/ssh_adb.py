"""SSH transport: drive adb on a remote machine WITHOUT deploying any agent.

Every agent capability is just "run an adb command on that machine and move
files around", so an SSH session with the user's credentials can do exactly
the same thing:

    adb devices -l                       (device discovery)
    adb -s <serial> shell <command>      (shell / listing / root / metrics)
    adb pull <path> <tmp> + SFTP get     (pull)
    SFTP put + adb push <tmp> <path>     (push)

The SSH session is created lazily, reused across calls and transparently
re-established after a failure.  Authentication accepts a password (entered
in the web UI) or falls back to the server's own SSH keys.

The connection targets an intranet tool, so the remote host key is not
verified on first contact (``known_hosts=None``); the credential is stored
per machine like the agent token is.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import uuid
from pathlib import Path

try:  # SSH direct mode is optional; the self-contained agent does not need it.
    import asyncssh
except ImportError:  # pragma: no cover - exercised by the dependency check
    asyncssh = None  # type: ignore[assignment]

from app.logger import logger
from app.transports.base import (
    DeviceEntry,
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
from app.transports.local_adb import parse_adb_devices


class SshAdbTransport(Transport):
    """Reach the adb of a remote machine over SSH (zero deployment)."""

    kind = "ssh"

    def __init__(
        self,
        host: str,
        ssh_port: int = 22,
        username: str = "root",
        password: str | None = None,
        adb_path: str = "adb",
        connect_timeout: float = 10.0,
        command_timeout: float = 30.0,
    ) -> None:
        self.host = host
        self.ssh_port = ssh_port
        self.username = username
        self.password = password
        self.adb_path = adb_path or "adb"
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        return f"ssh://{self.username}@{self.host}:{self.ssh_port}"

    # ------------------------------------------------------------ connection

    async def _connection(self) -> asyncssh.SSHClientConnection:
        if asyncssh is None:
            raise TransportError(
                "SSH 直连需要 Python 包 asyncssh：pip install asyncssh"
            )
        async with self._lock:
            if self._conn is not None:
                return self._conn
            try:
                self._conn = await asyncio.wait_for(
                    asyncssh.connect(
                        self.host,
                        port=self.ssh_port,
                        username=self.username,
                        password=self.password or None,
                        known_hosts=None,
                    ),
                    timeout=self.connect_timeout,
                )
            except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
                raise TransportError(
                    f"SSH 连接失败 {self.endpoint}: {exc}"
                ) from exc
            logger.info(f"SSH connection established: {self.endpoint}")
            return self._conn

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    async def close(self) -> None:
        self._drop()

    # ------------------------------------------------------------ execution

    async def _run(self, command: str, timeout: float | None = None) -> str:
        """Run one command on the remote machine and return stdout."""
        conn = await self._connection()
        try:
            result = await asyncio.wait_for(
                conn.run(command), timeout=timeout or self.command_timeout
            )
        except asyncio.TimeoutError as exc:
            raise TransportTimeoutError(f"SSH 命令超时: {command}") from exc
        except asyncssh.Error as exc:
            self._drop()
            raise TransportError(f"SSH 执行失败: {exc}") from exc

        output = result.stdout or ""
        # The same output classification as the agent channel: protected
        # directories answer as permission errors, not as generic failures.
        error = classify_output(output)
        if error is not None:
            raise error
        if result.exit_status != 0:
            message = (result.stderr or output).strip()
            raise TransportError(message or f"exit {result.exit_status}")
        return output

    def _adb_args(self, args: str) -> str:
        adb = shlex.quote(self.adb_path)
        return f"{adb} {args}"

    async def _adb(self, args: str, timeout: float | None = None) -> str:
        return await self._run(self._adb_args(args), timeout)

    async def _adb_shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        return await self._adb(
            f"-s {shlex.quote(serial)} shell {shlex.quote(command)}", timeout
        )

    # ------------------------------------------------------ abstract methods

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        return await self._adb_shell(serial, command, timeout)

    async def list_devices(self) -> list[DeviceEntry]:
        output = await self._adb("devices -l", timeout=self.command_timeout)
        entries = parse_adb_devices(output)
        for entry in entries:
            if entry.state == "device":
                entry.hardware_id = await self.device_hardware_id(entry.serial)
        return entries

    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        normalized = assert_safe_device_path(path)
        output = await self._adb_shell(
            serial,
            f"ls -la {shlex.quote(directory_listing_path(normalized))}",
            timeout=self.command_timeout,
        )
        return parse_long_listing(output, normalized)

    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        normalized = assert_safe_device_path(device_path)
        remote_tmp = f"/tmp/afe-pull-{uuid.uuid4().hex}"

        await self._adb(
            f"-s {shlex.quote(serial)} pull {shlex.quote(normalized)} {shlex.quote(remote_tmp)}",
            timeout=max(self.command_timeout, 600),
        )
        conn = await self._connection()
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote_tmp, local_path)
        except asyncssh.Error as exc:
            raise TransportError(f"SFTP 拉取失败: {exc}") from exc
        finally:
            try:
                await self._run(f"rm -f {shlex.quote(remote_tmp)}")
            except TransportError:  # pragma: no cover - cleanup must not fail the pull
                pass

        size = os.path.getsize(local_path)
        logger.info(f"ssh pull {normalized} -> {local_path} ({size} bytes)")
        return TransferResult(
            ok=True,
            path=normalized,
            size=size,
            stored_at=local_path,
            message="已拉取到服务器下载目录",
        )

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        source = Path(local_path)
        if not source.is_file():
            raise TransportError(f"本地文件不存在: {local_path}")
        normalized = assert_safe_device_path(device_path)
        remote_tmp = f"/tmp/afe-push-{uuid.uuid4().hex}"

        conn = await self._connection()
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(str(source), remote_tmp)
        except asyncssh.Error as exc:
            raise TransportError(f"SFTP 推送失败: {exc}") from exc

        try:
            await self._adb(
                f"-s {shlex.quote(serial)} push {shlex.quote(remote_tmp)} {shlex.quote(normalized)}",
                timeout=max(self.command_timeout, 600),
            )
        finally:
            try:
                await self._run(f"rm -f {shlex.quote(remote_tmp)}")
            except TransportError:  # pragma: no cover - cleanup must not fail the push
                pass

        size = source.stat().st_size
        logger.info(f"ssh push {source} -> {normalized} ({size} bytes)")
        return TransferResult(
            ok=True,
            path=normalized,
            size=size,
            stored_at=normalized,
            message="已推送到设备",
        )

    async def connect(self, serial: str) -> tuple[bool, str]:
        output = await self._adb(f"connect {shlex.quote(serial)}", timeout=30)
        lowered = output.lower()
        if "connected" in lowered and "cannot" not in lowered:
            return True, output.strip()
        return False, output.strip() or "连接失败"

    async def disconnect(self, serial: str) -> tuple[bool, str]:
        output = await self._adb(f"disconnect {shlex.quote(serial)}", timeout=30)
        return True, output.strip() or "已断开"

    async def adb_root(self, serial: str) -> str:
        return await self._adb(f"-s {shlex.quote(serial)} root", timeout=20)

    async def adb_unroot(self, serial: str) -> str:
        return await self._adb(f"-s {shlex.quote(serial)} unroot", timeout=20)
