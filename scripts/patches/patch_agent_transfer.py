"""One-shot patcher: implement FILE_PUSH / FILE_PULL in the device agent.

The self-contained agent replaced the external one, but only spoke bare adb
commands - the file transfer protocol (`FILE_PUSH:{json}` + raw bytes,
`FILE_PULL:{json}` -> FILE_INFO + FILE_DATA + raw bytes) was lost, so pull
and push regressed. This restores the wire protocol.

Security: the device path and the serial reach `adb push`/`adb pull` argument
lists on the agent machine. The same whitelist as the platform applies: no
shell metacharacters in device paths, a strict charset for serials. The
subprocess is invoked with an argv list, never through a shell.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
AGENT = ROOT / "agent" / "agent.py"
source = AGENT.read_text(encoding="utf-8")
edits = 0


def sub(old: str, new: str, label: str) -> None:
    global source, edits
    if old in source:
        source = source.replace(old, new, 1)
        edits += 1
        print(f"  {label} OK")
    elif new in source:
        print(f"  {label} already applied")
    else:
        print(f"  {label} ANCHOR MISSING")
        raise SystemExit(1)


# ---- imports -----------------------------------------------------------
sub(
    '''import argparse
import hmac
import logging''',
    '''import argparse
import hashlib
import hmac
import json
import logging
import re
import tempfile''',
    "imports",
)

# ---- the safe path helpers (same whitelist as the platform) -------------
sub(
    '''SEPARATOR = "-" * 50''',
    '''#: Same whitelist as the platform: `adb push`/`adb pull` arguments must not
#: carry shell metacharacters, and serials are limited to a strict charset.
FORBIDDEN_PATH_CHARS = set(";|&$`<>()\\"'\\n\\r\\t*?[]{}!#~%^,")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

SEPARATOR = "-" * 50''',
    "whitelist",
)

sub(
    '''def _resolve_adb(explicit: str | None) -> str:''',
    '''class AgentError(Exception):
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


def _resolve_adb(explicit: str | None) -> str:''',
    "helpers",
)

# ---- CommandRunner: argv based execution (no shell) ---------------------
sub(
    '''    def _env(self) -> dict[str, str]:''',
    '''    def run_argv(self, argv: list[str], timeout: float = COMMAND_TIMEOUT) -> CommandResult:
        """Run one adb command from an argument list (never through a shell)."""
        with self._lock:
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
            stdout = stdout[:MAX_OUTPUT] + "\\n... (输出已截断)"
        return CommandResult(completed.returncode, stdout, stderr)

    def _env(self) -> dict[str, str]:''',
    "run_argv",
)

# ---- handler: byte responses -------------------------------------------
sub(
    '''                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                self.wfile.write(self._dispatch(line).encode())
                self.wfile.flush()''',
    '''                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                response = self._dispatch(line)
                if response:
                    self.wfile.write(response)
                    self.wfile.flush()''',
    "byte handler",
)

sub(
    '''    def _dispatch(self, line: str) -> str:
        """Run one request line.

        The wire format is a bare adb command line: the platform sends
        ``adb devices -l`` or ``adb -s <serial> shell ls -la /sdcard`` and
        expects the envelope back.  ``PING`` is supported as a liveness probe.
        """
        upper = line.upper()
        if upper == "PING":
            return f"PONG\\n{FOOTER}\\n{END_MARKER}"
        if upper == "SHUTDOWN":
            # Let the agent be stopped through its own channel, so a
            # deployment does not depend on ssh access to the machine.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return format_envelope(CommandResult(0, "agent 正在停止", ""))''',
    '''    def _dispatch(self, line: str) -> bytes:
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
            return f"PONG\\n{FOOTER}\\n{END_MARKER}".encode()
        if upper == "SHUTDOWN":
            # Let the agent be stopped through its own channel, so a
            # deployment does not depend on ssh access to the machine.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return format_envelope(CommandResult(0, "agent 正在停止", "")).encode()
        if upper.startswith("FILE_PUSH:"):
            return self._handle_push(line[len("FILE_PUSH:") :])
        if upper.startswith("FILE_PULL:"):
            return self._handle_pull(line[len("FILE_PULL:") :])''',
    "dispatch head",
)

sub(
    '''        if not line.lower().startswith("adb "):
            return format_envelope(
                CommandResult(1, "", f"只允许 adb 命令，收到: {line}")
            )''',
    '''        if not line.lower().startswith("adb "):
            return format_envelope(
                CommandResult(1, "", f"只允许 adb 命令，收到: {line}")
            ).encode()''',
    "adb guard",
)

sub(
    '''        if len(parts) >= 3 and parts[1] in ("root", "unroot"):
            serial = None
            if parts[2] == "-s" and len(parts) >= 4:
                serial = parts[3]
            return format_envelope(self.runner.run_root_switch(line, serial))

        return format_envelope(self.runner.run(line))''',
    '''        if len(parts) >= 3 and parts[1] in ("root", "unroot"):
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
            return b"ERROR: FILE_PUSH 载荷不是合法 JSON\\n"

        try:
            remote_path = _safe_device_path(payload.get("remote_path", ""))
            serial = _safe_serial(payload.get("device_id"))
        except AgentError as exc:
            return f"ERROR: {exc}\\n".encode()

        size = int(payload.get("file_size", 0))
        expected_md5 = payload.get("md5", "")
        if size <= 0 or size > 1024 * 1024 * 1024:
            return f"ERROR: 文件大小非法: {size}\\n".encode()

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
            return f"ERROR: 接收文件失败: {exc}\\n".encode()

        if expected_md5 and digest.hexdigest() != expected_md5:
            os.unlink(tmp)
            return f"ERROR: MD5 校验失败\\n".encode()

        result = self.runner.run_argv(
            ["-s", serial, "push", tmp, remote_path], timeout=600
        )
        os.unlink(tmp)
        if result.returncode != 0:
            return f"ERROR: adb push 失败: {result.stderr.strip() or result.stdout.strip()}\\n".encode()
        return "OK:文件推送成功\\n".encode()

    def _handle_pull(self, payload_text: str) -> bytes:
        """FILE_PULL: `adb pull` on the agent machine + stream the bytes back."""
        try:
            payload = json.loads(payload_text)
        except ValueError:
            return b"ERROR: FILE_PULL 载荷不是合法 JSON\\n"

        try:
            remote_path = _safe_device_path(payload.get("remote_path", ""))
            serial = _safe_serial(payload.get("device_id"))
        except AgentError as exc:
            return f"ERROR: {exc}\\n".encode()

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = os.path.join(tmp_dir, "pulled.bin")
            result = self.runner.run_argv(
                ["-s", serial, "pull", remote_path, target], timeout=600
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip()
                return f"ERROR: adb pull 失败: {message}\\n".encode()

            size = os.path.getsize(target)
            name = remote_path.rsplit("/", 1)[-1] or "download.bin"
            digest = hashlib.md5()
            with open(target, "rb") as handle:
                digest_data = handle.read()
            digest.update(digest_data)

            self.wfile.write(f"FILE_INFO:FILE:{name}\\n".encode())
            self.wfile.write(f"FILE_DATA:{name}:{size}:{digest.hexdigest()}\\n".encode())
            self.wfile.write(digest_data)
            self.wfile.flush()
            return b""''',
    "transfer handlers",
)

# ---- os import ---------------------------------------------------------
if "\\nimport os\\n" not in source and "\\nimport os" not in source:
    pass
if "import os" not in source.split("class ")[0]:
    source = source.replace("import json\\n", "import json\\nimport os\\n", 1)
    print("  os import OK")

AGENT.write_text(source, encoding="utf-8")
print(f"DONE ({edits}/6)")
