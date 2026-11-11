"""Tests for the SSH adb transport.

The password authentication path is verified against an in-process asyncssh
server (deterministic, no external machines): a correct password reaches the
fake adb, a wrong one is refused before a single command runs.

The server lives on the SAME event loop as the test (pytest-asyncio), so the
SSH handshake is actually served.
"""

from __future__ import annotations

import asyncssh
import pytest
import pytest_asyncio

from app.transports.base import TransportError
from app.transports.ssh_adb import SshAdbTransport

FAKE_DEVICES = (
    "List of devices attached\nFAKE123\tdevice product:f model:Fake_X device:f\n"
)


class FakeAdbServer(asyncssh.SSHServer):
    """Accepts exactly one username/password pair."""

    def begin_auth(self, username: str) -> bool:
        return True  # password auth required

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "tester" and password == "secret"


async def _handle_client(process) -> None:
    command = process.command or ""
    if command == "adb devices -l":
        process.stdout.write(FAKE_DEVICES)
    elif command.startswith("adb -s FAKE123 shell "):
        process.stdout.write("FAKE-SHELL-OK\n")
    else:
        process.stdout.write("FAKE-OK\n")
    process.exit(0)


@pytest_asyncio.fixture
async def ssh_port():
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=FakeAdbServer,
        process_factory=_handle_client,
        server_host_keys=[host_key],
    )
    port = server.sockets[0].getsockname()[1]
    yield port
    server.close()


def _transport(port: int, password: str | None) -> SshAdbTransport:
    return SshAdbTransport(
        host="127.0.0.1",
        ssh_port=port,
        username="tester",
        password=password,
        connect_timeout=5,
        command_timeout=5,
    )


@pytest.mark.asyncio
async def test_the_password_grants_access_and_devices_are_parsed(ssh_port):
    transport = _transport(ssh_port, "secret")
    devices = await transport.list_devices()
    assert [d.serial for d in devices] == ["FAKE123"]
    assert devices[0].state == "device"
    assert devices[0].model == "Fake_X"
    await transport.close()


@pytest.mark.asyncio
async def test_a_wrong_password_is_refused_before_any_command(ssh_port):
    transport = _transport(ssh_port, "wrong")
    with pytest.raises(TransportError) as info:
        await transport.list_devices()
    assert "SSH 连接失败" in str(info.value)


@pytest.mark.asyncio
async def test_a_missing_password_falls_back_to_keys_and_fails_cleanly(ssh_port):
    transport = _transport(ssh_port, None)
    with pytest.raises(TransportError):
        await transport.list_devices()


@pytest.mark.asyncio
async def test_shell_runs_over_the_ssh_channel(ssh_port):
    transport = _transport(ssh_port, "secret")
    output = await transport.shell("FAKE123", "echo hi")
    assert "FAKE-SHELL-OK" in output
    await transport.close()
