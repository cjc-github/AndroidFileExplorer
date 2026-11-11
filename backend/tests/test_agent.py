"""Tests for the device agent.

The agent is a standalone component that runs on the remote machine, so it has
its own tests: the envelope format, the command whitelist and the root switch
handling.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

AGENT_FILE = Path(__file__).resolve().parent.parent.parent / "agent" / "agent.py"


def _load_agent():
    """Import the standalone agent file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("afe_agent", AGENT_FILE)
    assert spec and spec.loader, "cannot load the agent module"
    module = importlib.util.module_from_spec(spec)
    sys.modules["afe_agent"] = module
    spec.loader.exec_module(module)
    return module


agent = _load_agent()
CommandResult = agent.CommandResult
format_envelope = agent.format_envelope


def test_success_envelope_matches_the_wire_format():
    text = format_envelope(CommandResult(0, "hello\n", ""))
    assert text.startswith("命令执行成功 (返回码: 0)")
    assert "-" * 50 in text
    assert "标准输出:" in text
    assert "hello" in text
    assert text.rstrip().endswith("===END===")


def test_failure_envelope_uses_the_error_section():
    text = format_envelope(CommandResult(1, "", "boom"))
    assert text.startswith("命令执行失败 (返回码: 1)")
    assert "错误信息:" in text
    assert "boom" in text


def test_the_footer_is_the_last_line():
    lines = format_envelope(CommandResult(0, "x", "")).splitlines()
    assert lines[-1] == "===END==="
    assert lines[-2] == "=" * 50


def test_the_agent_only_runs_adb_commands():
    class FakeRunner:
        def run(self, command, timeout=30):
            if not command.startswith("adb "):
                return CommandResult(1, "", "只允许 adb 命令")
            return CommandResult(0, f"ran {command}", "")

    handler = object.__new__(agent.AgentHandler)
    handler.runner = FakeRunner()

    ok = handler._dispatch("adb devices").decode()
    assert "命令执行成功" in ok
    assert "ran adb devices" in ok

    denied = handler._dispatch("rm -rf /").decode()
    assert "命令执行失败" in denied
    assert "只允许 adb 命令" in denied


def test_ping_is_answered():
    class FakeRunner:
        def run(self, command, timeout=30):  # pragma: no cover - not used
            return CommandResult(0, "", "")

    handler = object.__new__(agent.AgentHandler)
    handler.runner = FakeRunner()
    assert "PONG" in handler._dispatch("PING").decode()


def test_root_switch_accepts_a_non_zero_return_code():
    """`adb root` restarts adbd, so adb always exits non zero on success."""
    runner = agent.CommandRunner.__new__(agent.CommandRunner)
    runner.adb_path = "adb"
    runner.adb_server = None
    runner._adb_slots = threading.BoundedSemaphore(1)
    runner.run = lambda command, timeout=30: CommandResult(
        1, "restarting adbd as root\n", ""
    )
    runner.wait_for_device = lambda serial, timeout=30: True

    result = runner.run_root_switch("adb -s emulator-5554 root", "emulator-5554")
    assert result.returncode == 0
    assert "restarting adbd as root" in result.stdout


def test_unroot_switch_is_recognised_too():
    runner = agent.CommandRunner.__new__(agent.CommandRunner)
    runner.adb_path = "adb"
    runner.adb_server = None
    runner._adb_slots = threading.BoundedSemaphore(1)
    runner.run = lambda command, timeout=30: CommandResult(
        1, "restarting adbd as non root\n", ""
    )
    runner.wait_for_device = lambda serial, timeout=30: True

    result = runner.run_root_switch("adb -s emulator-5554 unroot", "emulator-5554")
    assert result.returncode == 0


class _CountingWFile:
    """A wfile stand-in that remembers how many writes happened."""

    def __init__(self) -> None:
        import io

        self.buffer = io.BytesIO()
        self.writes = 0

    def write(self, data: bytes) -> int:
        self.writes += 1
        return self.buffer.write(data)

    def flush(self) -> None:
        pass

    def getvalue(self) -> bytes:
        return self.buffer.getvalue()


def test_file_pull_streams_the_payload_in_chunks(monkeypatch, tmp_path):
    """FILE_PULL must not buffer the whole file; it sends it chunk by chunk."""
    import hashlib
    import io
    import json
    import shutil

    payload = b"\x00\x01" * 10_000  # well over two chunks of 4096
    monkeypatch.setattr(agent, "PULL_CHUNK_SIZE", 4096)
    source = tmp_path / "big.bin"
    source.write_bytes(payload)

    class FakeRunner:
        def run_argv(self, argv, timeout=120):
            shutil.copyfile(source, argv[-1])
            return CommandResult(0, "pulled", "")

    handler = object.__new__(agent.AgentHandler)
    handler.runner = FakeRunner()
    handler.wfile = _CountingWFile()

    answer = handler._handle_pull(
        json.dumps({"remote_path": "/sdcard/big.bin", "device_id": "emulator-5554"})
    )

    assert answer == b"", "the payload goes to the stream, not the return value"
    out = handler.wfile.getvalue()
    head, data_header, body = out.split(b"\n", 2)
    assert head == b"FILE_INFO:FILE:big.bin"
    _prefix, size, md5_hex = data_header.decode().rsplit(":", 2)
    assert int(size) == len(payload)
    assert md5_hex == hashlib.md5(payload).hexdigest()
    assert body == payload
    # Two header writes plus one write per 4096 byte chunk: the payload was
    # streamed instead of written in one big piece.
    assert handler.wfile.writes >= 2 + len(payload) // 4096
    assert b"\x00\x01" not in io.BytesIO(answer).getvalue()
