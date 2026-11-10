"""A fake MDD engine server that speaks the real wire protocol.

Implements the three message families used by ``MddClient``:

* ``CMD``      – command in, output terminated by ``===END===``.
* ``FILE_PUSH`` – ``FILE_PUSH:{json}`` + raw bytes, answer ``OK``.
* ``FILE_PULL`` – ``FILE_PULL:{json}``, answer ``FILE_INFO:FILE:<name>`` +
  ``FILE_DATA:<name>:<size>:<md5>`` + raw bytes.

It keeps a tiny in-memory "device" so the transport layer can be exercised
end to end without a real Android device.
"""

from __future__ import annotations

import hashlib
import json
import socket
import socketserver
import threading
from pathlib import Path

END_MARKER = "===END==="


class FakeDevice:
    """In-memory device: a dict of path -> bytes."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {
            "/sdcard/hello.txt": b"hello from the fake device\n",
            "/sdcard/lib/libtest.so": b"\x7fELF fake shared object",
        }
        self.dirs: dict[str, list[str]] = {
            "/": ["sdcard"],
            "/sdcard": ["hello.txt", "lib"],
            "/sdcard/lib": ["libtest.so"],
        }
        self.commands: list[str] = []

    def ls(self, path: str) -> str:
        # A real file system ignores a trailing slash; the transport adds one
        # so that toybox lists a symlinked directory instead of describing it.
        normalized = path.rstrip("/") or "/"
        if normalized in ("/data", "/system"):
            return f"ls: {path}: Permission denied"
        path = normalized
        names = self.dirs.get(path)
        if names is None:
            return f"ls: {path}: No such file or directory"
        lines = ["total 8"]
        for name in names:
            child = f"{path.rstrip('/')}/{name}"
            if child in self.dirs:
                lines.append(f"drwxrwx--- 2 root u0_a12 4096 2024-01-01 12:00 {name}")
            else:
                content = self.files.get(child, b"")
                lines.append(
                    f"-rw-rw-rw- 1 root root {len(content):>6} 2024-01-02 13:45 {name}"
                )
        return "\n".join(lines)

    def read(self, path: str) -> bytes | None:
        return self.files.get(path)


class _Handler(socketserver.StreamRequestHandler):
    """Handles one client connection."""

    def handle(self) -> None:
        # StreamRequestHandler.setup() creates self.rfile / self.wfile.
        self.setup()
        device: FakeDevice = self.server.device  # type: ignore[attr-defined]
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                if text.startswith("FILE_PUSH:"):
                    self._handle_push(text[len("FILE_PUSH:") :], device)
                elif text.startswith("FILE_PULL:"):
                    self._handle_pull(text[len("FILE_PULL:") :], device)
                else:
                    self._handle_cmd(text, device)
            except Exception as exc:  # pragma: no cover - defensive
                self.wfile.write(f"ERROR:{exc}\n".encode())

    def _handle_cmd(self, command: str, device: FakeDevice) -> None:
        device.commands.append(command)
        output, failed = self._run_adb_like(command, device)
        if failed:
            body = (
                "命令执行失败 (返回码: 1)\n"
                "--------------------------------------------------\n"
                "错误信息:\n"
                f"{output}\n\n"
                "=================================================="
            )
        else:
            body = (
                "命令执行成功 (返回码: 0)\n"
                "--------------------------------------------------\n"
                "标准输出:\n"
                f"{output}\n\n"
                "=================================================="
            )
        self.wfile.write(f"{body}\n{END_MARKER}\n".encode())

    def _run_adb_like(self, command: str, device: FakeDevice) -> tuple[str, bool]:
        """Return (output, failed) like the real agent_server does."""
        if command.startswith("adb devices"):
            return "List of devices attached\nemulator-5554\tdevice", False
        if " shell ls " in command:
            path = command.rsplit(" ", 1)[-1]
            listing = device.ls(path)
            if listing.startswith("ls:"):
                return listing, True
            return listing, False
        if " shell " in command:
            return "", False
        return "", False

    def _handle_push(self, payload: str, device: FakeDevice) -> None:
        meta = json.loads(payload)
        size = int(meta["file_size"])
        remaining = size
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        remote = meta["remote_path"]
        device.files[remote] = content
        parent = str(Path(remote).parent)
        device.dirs.setdefault(parent, [])
        if Path(remote).name not in device.dirs[parent]:
            device.dirs[parent].append(Path(remote).name)
        self.wfile.write(b"OK\n")

    def _handle_pull(self, payload: str, device: FakeDevice) -> None:
        meta = json.loads(payload)
        remote = meta["remote_path"]
        content = device.read(remote)
        if content is None:
            self.wfile.write(f"ERROR:not found {remote}\n".encode())
            return
        name = Path(remote).name
        digest = hashlib.md5(content).hexdigest()
        self.wfile.write(f"FILE_INFO:FILE:{name}\n".encode())
        self.wfile.write(f"FILE_DATA:{name}:{len(content)}:{digest}\n".encode())
        self.wfile.write(content)


class FakeMddServer(socketserver.ThreadingTCPServer):
    """Threaded fake MDD engine."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.device = FakeDevice()
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def shutdown_server(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)
