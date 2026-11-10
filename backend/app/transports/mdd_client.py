"""Minimal MDD (agent_server) TCP client.

The wire protocol follows ``apk_survey/fuzzing/device_client.py``:

* ``CMD``  – send ``<command>\\n`` and read until the ``===END===`` marker.
* ``FILE_PUSH`` – ``FILE_PUSH:{json}\\n`` + raw bytes, server answers ``OK\\n``.
* ``FILE_PULL`` – ``FILE_PULL:{json}\\n``, server answers ``FILE_INFO:<type>:<name>``
  followed by ``FILE_DATA:<name>:<size>:<md5>`` + raw bytes.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

from .agent_protocol import unwrap_agent_output

END_MARKER = "===END==="
DEFAULT_BUFFER_SIZE = 8192


class MddError(RuntimeError):
    """Base MDD protocol error."""


class MddConnectionError(MddError):
    """The engine could not be reached."""


class MddTimeoutError(MddError):
    """The engine did not answer in time."""


class MddProtocolError(MddError):
    """The engine answered something unexpected."""


@dataclass(slots=True)
class MddCommandResult:
    """Outcome of a remote command execution."""

    success: bool
    message: str
    error: str | None = None


class MddClient:
    """Small synchronous MDD client; wrap it with ``asyncio.to_thread``."""

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
        self._socket: socket.socket | None = None

    # ------------------------------------------------------------------ socket

    def connect(self) -> None:
        """Open the TCP connection to the engine."""
        if self._socket is not None:
            return
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            )
        except OSError as exc:
            raise MddConnectionError(f"连接引擎失败: {exc}") from exc
        sock.settimeout(self.command_timeout)
        self._socket = sock
        if self.token:
            self._authenticate(sock)

    def _authenticate(self, sock: socket.socket) -> None:
        """Send the shared secret and check the answer."""
        sock.sendall(f"AUTH {self.token}\n".encode())
        answer = sock.recv(256).decode(errors="replace").strip()
        if not answer.startswith("OK"):
            try:
                sock.close()
            finally:
                self._socket = None
            raise MddConnectionError(f"agent 认证失败: {answer}")

    def close(self) -> None:
        """Close the TCP connection."""
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def __enter__(self) -> "MddClient":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise MddConnectionError("引擎未连接")
        return self._socket

    def _read_line(self) -> str:
        sock = self._require_socket()
        buffer = b""
        while True:
            chunk = sock.recv(1)
            if not chunk:
                raise MddConnectionError("连接在读取响应前关闭")
            buffer += chunk
            if chunk == b"\n":
                break
        return buffer.decode(errors="replace").strip()

    def _read_until_end(self) -> str:
        sock = self._require_socket()
        buffer = b""
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                raise MddConnectionError("连接在读取响应前关闭")
            buffer += chunk
            if END_MARKER.encode() in buffer:
                break
        text = buffer.decode(errors="replace")
        return text.replace(END_MARKER, "").strip()

    # ----------------------------------------------------------------- command

    def execute_command(self, command: str, timeout: float | None = None) -> MddCommandResult:
        """Run a command on the engine host and return its output."""
        sock = self._require_socket()
        previous_timeout = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            payload = command if command.endswith("\n") else command + "\n"
            sock.sendall(payload.encode())
            response = self._read_until_end()
        except socket.timeout as exc:
            raise MddTimeoutError(f"引擎命令超时: {command}") from exc
        except OSError as exc:
            raise MddConnectionError(f"引擎通信失败: {exc}") from exc
        finally:
            sock.settimeout(previous_timeout)

        if response.startswith("ERROR:"):
            return MddCommandResult(success=False, message=response, error=response)

        ok, output = unwrap_agent_output(response)
        if not ok:
            return MddCommandResult(success=False, message=output, error=output)
        return MddCommandResult(success=True, message=output)

    # -------------------------------------------------------------------- push

    def push_file(
        self,
        local_path: str,
        remote_path: str,
        device_id: str | None = None,
        timeout: float | None = None,
    ) -> MddCommandResult:
        """Upload a local file to the engine host (and onward to the device)."""
        import hashlib
        import os

        if not os.path.isfile(local_path):
            return MddCommandResult(success=False, message=f"文件不存在: {local_path}", error="not found")

        file_size = os.path.getsize(local_path)
        md5_hash = hashlib.md5()
        with open(local_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                md5_hash.update(chunk)

        sock = self._require_socket()
        previous_timeout = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            payload = {
                "filename": os.path.basename(local_path),
                "file_size": file_size,
                "remote_path": remote_path,
                "md5": md5_hash.hexdigest(),
            }
            if device_id:
                payload["device_id"] = device_id
            header = f"FILE_PUSH:{json.dumps(payload, ensure_ascii=False)}\n"
            sock.sendall(header.encode())
            if file_size:
                with open(local_path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(DEFAULT_BUFFER_SIZE), b""):
                        sock.sendall(chunk)
            response = self._read_line()
        except socket.timeout as exc:
            raise MddTimeoutError(f"引擎推送超时: {remote_path}") from exc
        except OSError as exc:
            raise MddConnectionError(f"引擎通信失败: {exc}") from exc
        finally:
            sock.settimeout(previous_timeout)

        ok = response.startswith("OK")
        return MddCommandResult(
            success=ok,
            message=response,
            error=None if ok else response,
        )

    # -------------------------------------------------------------------- pull

    def pull_file(
        self,
        remote_path: str,
        local_path: str,
        device_id: str | None = None,
        timeout: float | None = None,
    ) -> MddCommandResult:
        """Download a file from the engine host (and from the device)."""
        import hashlib
        import os

        sock = self._require_socket()
        previous_timeout = sock.gettimeout()
        if timeout is not None:
            sock.settimeout(timeout)
        try:
            payload = {"remote_path": remote_path, "local_path": local_path, "device_id": device_id}
            header = f"FILE_PULL:{json.dumps(payload, ensure_ascii=False)}\n"
            sock.sendall(header.encode())

            info = self._read_line()
            if not info.startswith("FILE_INFO:"):
                return MddCommandResult(success=False, message=info, error=info)

            parts = info.split(":")
            source_type = parts[1] if len(parts) > 1 else ""
            source_name = ":".join(parts[2:]) if len(parts) > 2 else ""
            if source_type != "FILE":
                return MddCommandResult(
                    success=False,
                    message=f"暂不支持拉取目录: {remote_path}",
                    error="unsupported source type",
                )

            data_header = self._read_line()
            if not data_header.startswith("FILE_DATA:"):
                return MddCommandResult(success=False, message=data_header, error=data_header)

            _, file_size, expected_md5 = data_header[len("FILE_DATA:") :].rsplit(":", 2)
            file_size_int = int(file_size)

            target_dir = os.path.dirname(os.path.abspath(local_path))
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)

            digest = hashlib.md5()
            received = 0
            with open(local_path, "wb") as handle:
                while received < file_size_int:
                    chunk = sock.recv(min(DEFAULT_BUFFER_SIZE, file_size_int - received))
                    if not chunk:
                        raise MddConnectionError("拉取过程中连接关闭")
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)

            sock.sendall(b"OK\n")
        except socket.timeout as exc:
            raise MddTimeoutError(f"引擎拉取超时: {remote_path}") from exc
        except OSError as exc:
            raise MddConnectionError(f"引擎通信失败: {exc}") from exc
        finally:
            sock.settimeout(previous_timeout)

        actual_md5 = digest.hexdigest()
        if expected_md5 and actual_md5 != expected_md5:
            return MddCommandResult(
                success=False,
                message=f"MD5 校验失败: 期望 {expected_md5} 实际 {actual_md5}",
                error="md5 mismatch",
            )

        return MddCommandResult(
            success=True,
            message=f"{source_name} ({file_size_int} bytes)",
        )
