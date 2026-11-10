"""Regression test for the brand cache.

The brand cache was introduced as an attribute of ``Transport.__init__``, but
the concrete transports do not all call ``super().__init__()``. The cache is
therefore created lazily; this test locks that behaviour in.
"""

from __future__ import annotations

import pytest

from app.transports.base import Transport


class NoSuperTransport(Transport):
    """A transport that does NOT call super().__init__()."""

    kind = "test"

    def __init__(self) -> None:  # noqa: D107 - deliberately no super() call
        self.calls = 0

    @property
    def endpoint(self) -> str:
        return "test:1"

    async def list_devices(self):
        return []

    async def shell(self, serial, command, timeout=None):
        self.calls += 1
        return "google"

    async def list_dir(self, serial, path):
        return []

    async def pull(self, serial, device_path, local_path):
        raise NotImplementedError

    async def push(self, serial, local_path, device_path):
        raise NotImplementedError

    async def connect(self, serial):
        return True, "ok"

    async def disconnect(self, serial):
        return True, "ok"


@pytest.mark.asyncio
async def test_brand_is_read_once_and_cached():
    transport = NoSuperTransport()
    first = await transport.device_brand("serial-1")
    second = await transport.device_brand("serial-1")
    assert first == "google"
    assert second == "google"
    assert transport.calls == 1, "the brand must be read only once per device"


@pytest.mark.asyncio
async def test_brand_cache_is_per_device():
    transport = NoSuperTransport()
    await transport.device_brand("serial-1")
    await transport.device_brand("serial-2")
    assert transport.calls == 2
