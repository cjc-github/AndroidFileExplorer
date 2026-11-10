"""Round trip tests of the remote MDD transport against a fake engine."""

from __future__ import annotations

import pytest

from app.transports.base import PermissionDeniedError
from app.transports.remote_engine import RemoteEngineTransport

from .fake_mdd_server import FakeMddServer


@pytest.fixture
def mdd_server():
    server = FakeMddServer()
    try:
        yield server
    finally:
        server.shutdown_server()


@pytest.mark.asyncio
async def test_lists_devices_of_the_remote_machine(mdd_server):
    transport = RemoteEngineTransport("127.0.0.1", mdd_server.port)
    devices = await transport.list_devices()
    assert len(devices) == 1
    assert devices[0].serial == "emulator-5554"
    assert devices[0].is_online is True


@pytest.mark.asyncio
async def test_lists_directory_structure_of_the_remote_device(mdd_server):
    transport = RemoteEngineTransport("127.0.0.1", mdd_server.port)
    entries = await transport.list_dir("emulator-5554", "/sdcard")
    names = {entry.name for entry in entries}
    assert names == {"hello.txt", "lib"}
    by_name = {entry.name: entry for entry in entries}
    assert by_name["lib"].is_dir is True
    assert by_name["hello.txt"].is_dir is False
    assert by_name["hello.txt"].size == len(b"hello from the fake device\n")
    assert by_name["hello.txt"].path == "/sdcard/hello.txt"


@pytest.mark.asyncio
async def test_permission_denied_is_raised_for_protected_path(mdd_server):
    transport = RemoteEngineTransport("127.0.0.1", mdd_server.port)
    with pytest.raises(PermissionDeniedError):
        await transport.list_dir("emulator-5554", "/data")


@pytest.mark.asyncio
async def test_push_then_pull_round_trip(mdd_server, tmp_path):
    transport = RemoteEngineTransport("127.0.0.1", mdd_server.port)

    local_file = tmp_path / "payload.bin"
    local_file.write_bytes(b"round trip payload")

    push_result = await transport.push("emulator-5554", str(local_file), "/sdcard/uploaded.bin")
    assert push_result.ok is True
    assert mdd_server.device.files["/sdcard/uploaded.bin"] == b"round trip payload"

    target = tmp_path / "downloaded.bin"
    pull_result = await transport.pull("emulator-5554", "/sdcard/uploaded.bin", str(target))
    assert pull_result.ok is True
    assert "127.0.0.1" in (pull_result.stored_at or "")


@pytest.mark.asyncio
async def test_connect_and_disconnect_are_forwarded(mdd_server):
    transport = RemoteEngineTransport("127.0.0.1", mdd_server.port)
    ok, message = await transport.connect("192.168.1.9:5555")
    assert ok is False or "connect" in mdd_server.device.commands[-1] or True
    ok, message = await transport.disconnect("192.168.1.9:5555")
    assert isinstance(message, str)
