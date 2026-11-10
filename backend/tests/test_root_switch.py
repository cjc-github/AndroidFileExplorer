"""Regression tests for the root switch and the batch operation.

The root switch has three tricky behaviours that are easy to break:

* ``adb root`` restarts adbd, so the RETURN CODE is not reliable - the text
  is the only signal.
* ``adb unroot`` on a device that is not rooted is NOT an error, the request
  is simply already satisfied.
* A batch operation must report every device, even the ones that failed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.transports.base import Transport


class RootTransport(Transport):
    """A transport with a scripted answer for the root switch."""

    kind = "test"

    def __init__(self, root_output: str, unroot_output: str) -> None:
        self.root_output = root_output
        self.unroot_output = unroot_output
        self.calls: list[str] = []
        self.online = True

    @property
    def endpoint(self) -> str:
        return "test:fake"

    async def list_devices(self):  # pragma: no cover - not used
        return []

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        self.calls.append(command)
        if not self.online:
            raise RuntimeError("offline")
        return "uid=0(root)"

    async def list_dir(self, serial: str, path: str):  # pragma: no cover - not used
        return []

    async def pull(self, serial: str, device_path: str, local_path: str):  # pragma: no cover
        raise NotImplementedError

    async def push(self, serial: str, local_path: str, device_path: str):  # pragma: no cover
        raise NotImplementedError

    async def connect(self, serial: str):  # pragma: no cover - not used
        return True, ""

    async def disconnect(self, serial: str):  # pragma: no cover - not used
        return True, ""

    async def metrics(self, serial: str):  # pragma: no cover - not used
        raise NotImplementedError

    async def adb_root(self, serial: str) -> str:
        self.calls.append("adb root")
        return self.root_output

    async def adb_unroot(self, serial: str) -> str:
        self.calls.append("adb unroot")
        return self.unroot_output


def run(coro):
    return asyncio.run(coro)


def test_a_non_zero_return_code_still_counts_as_a_success():
    """`adb root` always exits non zero because adbd restarts."""
    transport = RootTransport("restarting adbd as root\n", "")
    ok, message = run(transport.set_root("serial", True))
    assert ok is True
    assert "root" in message


def test_unrooting_an_unrooted_device_is_not_an_error():
    transport = RootTransport("", "adbd not running as root")
    ok, message = run(transport.set_root("serial", False))
    assert ok is True
    assert "未以 root" in message


def test_a_production_device_is_refused():
    transport = RootTransport("adbd cannot run as root in production builds", "")
    ok, message = run(transport.set_root("serial", True))
    assert ok is False
    assert "生产版本" in message


def test_an_unknown_output_is_an_error():
    transport = RootTransport("something unexpected", "")
    ok, _ = run(transport.set_root("serial", True))
    assert ok is False


def test_the_device_is_awaited_after_the_switch():
    """The device is offline right after the restart, so it must be awaited."""
    transport = RootTransport("restarting adbd as root\n", "")

    seen: list[bool] = []

    async def scenario():
        async def fake_shell(serial, command, timeout=None):
            seen.append(True)
            return "uid=0(root)"

        transport.shell = fake_shell  # type: ignore[assignment]
        await transport.set_root("serial", True)

    run(scenario())
    # The wait sends at least one probe command.
    assert seen
