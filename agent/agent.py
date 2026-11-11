"""AndroidFileExplorer device agent.

A tiny, dependency free agent that runs on a machine with Android devices
attached and exposes them to the platform over a small line based TCP
protocol.  The platform (backend/app/transports/remote_engine.py) speaks the
very same protocol, so the two halves fit together without any third party
component.

The agent only needs the Python standard library and ``adb``:

    python3 agent/agent.py --host 0.0.0.0 --port 12345

Protocol
--------
A request is one line; the answer is the envelope below followed by the
``===END===`` marker:

    命令执行成功 (返回码: 0)
    --------------------------------------------------
    标准输出:
    <stdout>

    ==================================================
    ===END===

A failing command uses ``命令执行失败`` and the ``错误信息:`` section instead.

Only ``adb ...`` commands are accepted - the agent is a device bridge, not a
general purpose shell.  File transfer speaks a small binary protocol
(``FILE_PUSH`` / ``FILE_PULL``) and streams the payload in chunks, so a
multi-gigabyte pull never lands in the agent's memory at once.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import re
import tempfile
import os
import shlex
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from typing import NamedTuple

LOGGER = logging.getLogger("afe.agent")

DEFAULT_PORT = 12345
DEFAULT_ADB = "adb"
COMMAND_TIMEOUT = 120.0
MAX_OUTPUT = 512 * 1024
DEFAULT_ADB_CONCURRENCY = 8
#: FILE_PULL streams the pulled file back in chunks of this size instead of
#: buffering the whole payload in memory.
PULL_CHUNK_SIZE = 1024 * 1024

#: Same whitelist as the platform: `adb push`/`adb pull` arguments must not
#: carry shell metacharacters, and serials are limited to a strict charset.
FORBIDDEN_PATH_CHARS = set(";|&$`<>()\"'\n\r\t*?[]{}!#~%^,")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

SEPARATOR = "-" * 50
FOOTER = "=" * 50
END_MARKER = "===END==="


class CommandResult(NamedTuple):
    """Outcome of one adb command."""

    returncode: int
    stdout: str
    stderr: str


def format_envelope(result: CommandResult) -> str:
    """Wrap a command result the way the platform expects it."""
    if result.returncode == 0:
        body = result.stdout if result.stdout.strip() else result.stderr
        head = "命令执行成功 (返回码: 0)"
        label = "标准输出:"
    else:
        body = result.stderr if result.stderr.strip() else result.stdout
        head = f"命令执行失败 (返回码: {result.returncode})"
        label = "错误信息:"

    return f"{head}\n{SEPARATOR}\n{label}\n{body.strip()}\n\n{FOOTER}\n{END_MARKER}"


class AgentError(Exception):
    """A request that cannot be served (answered as an ERROR line)."""


def _safe_device_path(path: str) -> str:
    normalized = (path or "").strip()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = posixpath_norm(normalized)
    if normalized != "/":
        for segment in normalized.split("/"):
            if segment and (FORBIDDEN_PATH_CHARS & set(segment)):
                raise AgentError(f"路径包含非法字符: {normalized}")
    return normalized


def posixpath_norm(path: str) -> str:
    parts = [p for p in path.split("/") if p and p != "."]
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return "/" + "/".join(out)


def _safe_serial(serial: str | None) -> str:
    if not serial or not SERIAL_RE.match(serial):
        raise AgentError(f"非法设备序列号: {serial!r}")
    return serial


def _resolve_adb(explicit: str | None) -> str:
    """Find the adb executable."""
    candidate = explicit or os.getenv("AFE_ADB_PATH") or DEFAULT_ADB
    resolved = shutil.which(candidate)
    if resolved is None:
        raise RuntimeError(
            f"找不到 adb 命令（{candidate}）。请安装 Android platform-tools "
            "或用 --adb 指定路径。"
        )
    return resolved


class CommandRunner:
    """Runs adb commands on behalf of the connected clients.

    adb can run several commands at once (one process per command), so the
    runner does NOT serialize everything behind one lock any more - commands
    for different devices overlap.  A semaphore caps how many adb processes
    run at the same time so a burst of clients cannot explode the machine.
    """

    def __init__(
        self,
        adb_path: str,
        adb_server: tuple[str, int] | None = None,
        max_concurrency: int = DEFAULT_ADB_CONCURRENCY,
    ) -> None:
        self.adb_path = adb_path
        self.adb_server = adb_server
        self._adb_slots = threading.BoundedSemaphore(max(1, max_concurrency))

    def wait_for_device(self, serial: str | None, timeout: float = 30.0) -> bool:
        """Wait until the device shows up again.

        ``adb root`` / ``adb unroot`` restart adbd, so the device disappears for
        a few seconds.  Without this wait every command issued right after the
        restart fails with "timeout expired while waiting for device".
        """
        deadline = time.monotonic() + timeout
        target = f"-s {shlex.quote(serial)} " if serial else ""
        while time.monotonic() < deadline:
            code, stdout, _ = self.run(f"adb {target}get-state", timeout=5)
            if code == 0 and stdout.strip() == "device":
                return True
            time.sleep(1.0)
        return False

    def run_root_switch(self, command: str, serial: str | None) -> CommandResult:
        """Run ``adb root`` / ``adb unroot`` and wait for the device.

        ``adb root`` restarts adbd, so adb ALWAYS exits non zero even when the
        switch worked.  The output text is the only reliable signal, so the
        return code is corrected here.
        """
        result = self.run(command, timeout=30)
        text = f"{result.stdout}\n{result.stderr}".lower()
        switched = (
            "restarting adbd as root" in text
            or "already running as root" in text
            or "restarting adbd as non root" in text
            or "already running as non root" in text
        )
        if switched:
            result = CommandResult(0, result.stdout.strip() or result.stderr.strip(), "")
            self.wait_for_device(serial, timeout=30)
        return result

    def run_argv(self, argv: list[str], timeout: float = COMMAND_TIMEOUT) -> CommandResult:
        """Run one adb command from an argument list (never through a shell)."""
        with self._adb_slots:
            try:
                completed = subprocess.run(
                    [self.adb_path, *argv],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=self._env(),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return CommandResult(124, "", f"命令超时: {' '.join(argv)}")
            except OSError as exc:
                return CommandResult(1, "", f"执行失败: {exc}")
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (输出已截断)"
        return CommandResult(completed.returncode, stdout, stderr)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.adb_server:
            host, port = self.adb_server
            env["ADB_SERVER_SOCKET"] = f"tcp:{host}:{port}"
        return env

    def run(self, command: str, timeout: float = COMMAND_TIMEOUT) -> CommandResult:
        """Run one adb command."""
        try:
            parts = shlex.split(command)
        except ValueError:
            return CommandResult(1, "", f"命令格式错误: {command}")

        if not parts:
            return CommandResult(0, "", "")
        if parts[0] != "adb":
            # Only adb commands are allowed: the agent is a device bridge.
            return CommandResult(1, "", f"只允许 adb 命令，收到: {command}")

        argv = [self.adb_path, *parts[1:]]
        with self._adb_slots:
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=self._env(),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return CommandResult(124, "", f"命令超时: {command}")
            except OSError as exc:
                return CommandResult(1, "", f"执行失败: {exc}")

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n... (输出已截断)"
        return CommandResult(completed.returncode, stdout, stderr)


class AgentHandler(socketserver.StreamRequestHandler):
    """One client connection."""

    runner: CommandRunner
    token: str | None = None

    def handle(self) -> None:
        LOGGER.info("client connected: %s", self.client_address)
        try:
            if not self._authenticate():
                LOGGER.warning(
                    "client %s failed the authentication", self.client_address
                )
                return
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                response = self._dispatch(line)
                if response:
                    self.wfile.write(response)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            LOGGER.info("client disconnected: %s", self.client_address)

    def _authenticate(self) -> bool:
        """Check the shared secret.

        The client sends ``AUTH <token>`` as the first line.  Without a
        configured token the handshake is skipped.
        """
        if not self.token:
            return True
        raw = self.rfile.readline()
        if not raw:
            return False
        line = raw.decode(errors="replace").strip()
        keyword, _, value = line.partition(" ")
        if keyword.upper() != "AUTH" or not hmac.compare_digest(
            value.strip(), self.token
        ):
            self.wfile.write(b"ERROR: authentication failed\n")
            self.wfile.flush()
            return False
        self.wfile.write(b"OK\n")
        self.wfile.flush()
        return True

    def _dispatch(self, line: str) -> bytes:
        """Run one request line and answer with raw bytes.

        Bare adb commands get the envelope; ``FILE_PUSH`` / ``FILE_PULL``
        speak the binary transfer protocol; ``PING`` is a liveness probe and
        ``OK`` is the transfer acknowledgement of the platform.
        """
        upper = line.upper()
        if upper == "OK":
            # The platform acknowledges a completed FILE_PULL; nothing to do.
            return b""
        if upper == "PING":
            return f"PONG\n{FOOTER}\n{END_MARKER}".encode()
        if upper == "SHUTDOWN":
            # Let the agent be stopped through its own channel, so a
            # deployment does not depend on ssh access to the machine.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return format_envelope(CommandResult(0, "agent 正在停止", "")).encode()
        if upper.startswith("FILE_PUSH:"):
            return self._handle_push(line[len("FILE_PUSH:") :])
        if upper.startswith("FILE_PULL:"):
            return self._handle_pull(line[len("FILE_PULL:") :])
        if not line.lower().startswith("adb "):
            return format_envelope(
                CommandResult(1, "", f"只允许 adb 命令，收到: {line}")
            ).encode()

        # `adb root` / `adb unroot` restart adbd: wait for the device to come
        # back, otherwise the caller sees a timeout instead of the result.
        parts = line.split()
        if len(parts) >= 3 and parts[1] in ("root", "unroot"):
            serial = None
            if parts[2] == "-s" and len(parts) >= 4:
                serial = parts[3]
            return format_envelope(self.runner.run_root_switch(line, serial)).encode()

        return format_envelope(self.runner.run(line)).encode()

    # ------------------------------------------------------------ transfers

    def _handle_push(self, payload_text: str) -> bytes:
        """FILE_PUSH: header line + raw bytes -> `adb push` onto the device."""
        try:
            payload = json.loads(payload_text)
        except ValueError:
            return "ERROR: FILE_PUSH 载荷不是合法 JSON\n".encode()

        try:
            remote_path = _safe_device_path(payload.get("remote_path", ""))
            serial = _safe_serial(payload.get("device_id"))
        except AgentError as exc:
            return f"ERROR: {exc}\n".encode()

        size = int(payload.get("file_size", 0))
        expected_md5 = payload.get("md5", "")
        if size <= 0 or size > 1024 * 1024 * 1024:
            return f"ERROR: 文件大小非法: {size}\n".encode()

        digest = hashlib.md5()
        received = 0
        try:
            with tempfile.NamedTemporaryFile(delete=False) as handle:
                tmp = handle.name
                while received < size:
                    chunk = self.rfile.read(min(65536, size - received))
                    if not chunk:
                        raise AgentError("客户端提前断开")
                    handle.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
        except (AgentError, OSError) as exc:
            return f"ERROR: 接收文件失败: {exc}\n".encode()

        if expected_md5 and digest.hexdigest() != expected_md5:
            os.unlink(tmp)
            return f"ERROR: MD5 校验失败\n".encode()

        result = self.runner.run_argv(
            ["-s", serial, "push", tmp, remote_path], timeout=600
        )
        os.unlink(tmp)
        if result.returncode != 0:
            return f"ERROR: adb push 失败: {result.stderr.strip() or result.stdout.strip()}\n".encode()
        return "OK:文件推送成功\n".encode()

    def _handle_pull(self, payload_text: str) -> bytes:
        """FILE_PULL: `adb pull` on the agent machine + stream the bytes back."""
        try:
            payload = json.loads(payload_text)
        except ValueError:
            return "ERROR: FILE_PULL 载荷不是合法 JSON\n".encode()

        try:
            remote_path = _safe_device_path(payload.get("remote_path", ""))
            serial = _safe_serial(payload.get("device_id"))
        except AgentError as exc:
            return f"ERROR: {exc}\n".encode()

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = os.path.join(tmp_dir, "pulled.bin")
            result = self.runner.run_argv(
                ["-s", serial, "pull", remote_path, target], timeout=600
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip()
                return f"ERROR: adb pull 失败: {message}\n".encode()

            size = os.path.getsize(target)
            name = remote_path.rsplit("/", 1)[-1] or "download.bin"
            # The md5 travels in the header that precedes the bytes, so the
            # digest needs one streaming pass before the payload is sent.
            # Both passes read fixed size chunks: the payload is never fully
            # buffered in memory.
            digest = hashlib.md5()
            with open(target, "rb") as handle:
                while True:
                    chunk = handle.read(PULL_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)

            self.wfile.write(f"FILE_INFO:FILE:{name}\n".encode())
            self.wfile.write(f"FILE_DATA:{name}:{size}:{digest.hexdigest()}\n".encode())
            with open(target, "rb") as handle:
                while True:
                    chunk = handle.read(PULL_CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            self.wfile.flush()
            return b""


class AgentServer(socketserver.ThreadingTCPServer):
    """Threaded TCP server for the agent."""

    allow_reuse_address = True
    daemon_threads = True


def build_server(
    host: str,
    port: int,
    adb_path: str | None = None,
    adb_server: tuple[str, int] | None = None,
    token: str | None = None,
    max_concurrency: int = DEFAULT_ADB_CONCURRENCY,
) -> AgentServer:
    runner = CommandRunner(
        _resolve_adb(adb_path),
        adb_server,
        max_concurrency=max_concurrency,
    )
    handler = type(
        "BoundAgentHandler", (AgentHandler,), {"runner": runner, "token": token}
    )
    return AgentServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AndroidFileExplorer device agent (adb bridge)"
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--adb", default=None, help="adb 可执行文件路径")
    parser.add_argument(
        "--adb-server",
        default=None,
        help="远端 adb server，形如 host:port（默认用本机 adb server）",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=int(os.getenv("AFE_ADB_MAX_CONCURRENT", DEFAULT_ADB_CONCURRENCY)),
        help="同时运行的 adb 进程上限（默认 8，可用 AFE_ADB_MAX_CONCURRENT）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="共享密钥：客户端需先发送 AUTH <token>（可用 AFE_AGENT_TOKEN 环境变量）",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="日志级别 (DEBUG/INFO/WARNING)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    adb_server: tuple[str, int] | None = None
    if args.adb_server:
        host, _, port = args.adb_server.partition(":")
        try:
            adb_server = (host, int(port))
        except ValueError:
            print(f"--adb-server 格式错误: {args.adb_server}", file=sys.stderr)
            return 2

    token = args.token or os.getenv("AFE_AGENT_TOKEN") or None
    if token:
        LOGGER.info("agent authentication is enabled")

    try:
        server = build_server(
            args.host, args.port, args.adb, adb_server, token, args.max_concurrent
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    LOGGER.info("agent listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("agent stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
