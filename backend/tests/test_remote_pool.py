"""The remote engine keeps a pool of connections per machine.

Commands for different devices must not queue behind one socket; a burst of
polls and a user action should overlap. These tests use a counting fake
engine server that records how many client connections were active at the
same time.
"""

from __future__ import annotations

import socketserver
import threading
import time

import pytest

from app.transports.base import TransportError
from app.transports.mdd_client import MddClient
from app.transports.remote_engine import RemoteEngineTransport

END_MARKER = "===END==="
ENVELOPE = (
    "命令执行成功 (返回码: 0)\n"
    f"{'-' * 50}\n"
    "标准输出:\n"
    "ok\n\n"
    f"{'=' * 50}\n"
    f"{END_MARKER}\n"
).encode()


class _CountingHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server  # type: ignore[attr-defined]
        with server._counter_lock:
            server._active += 1
            server._peak = max(server._peak, server._active)
        try:
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                text = line.decode(errors="replace").strip()
                if text.startswith("AUTH"):
                    self.wfile.write(b"OK\n")
                    continue
                time.sleep(0.05)  # widen the window so overlap is observable
                self.wfile.write(ENVELOPE)
        finally:
            with server._counter_lock:
                server._active -= 1


class CountingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CountingHandler)
        self._counter_lock = threading.Lock()
        self._active = 0
        self._peak = 0
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def counting_server():
    server = CountingServer()
    yield server
    server.stop()


def test_concurrent_commands_use_several_connections(counting_server):
    """Four parallel commands overlap on the machine, not one by one."""
    transport = RemoteEngineTransport(
        "127.0.0.1", counting_server.port, pool_size=4, command_timeout=10.0
    )
    threads = [
        threading.Thread(
            target=lambda index=index: transport._execute(f"cmd {index}")
        )
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert transport._opened <= 4
    assert counting_server._peak >= 2, "commands must overlap, not serialize"

    transport._reset_failure()


def test_pool_reuses_idle_connections(counting_server):
    """Sequential commands stay on one pooled connection."""
    transport = RemoteEngineTransport(
        "127.0.0.1", counting_server.port, pool_size=4, command_timeout=10.0
    )
    for index in range(3):
        assert transport._execute(f"cmd {index}") == "ok"
    assert transport._opened == 1
    assert counting_server._peak == 1
    transport._reset_failure()


def test_broken_connection_is_dropped_and_replaced(counting_server):
    """A dying connection leaves the pool; a later command reconnects."""
    from app.transports.mdd_client import MddClient
    from app.transports.base import TransportError

    transport = RemoteEngineTransport(
        "127.0.0.1", counting_server.port, pool_size=2, command_timeout=10.0
    )
    # Poison the pool with a client whose socket points nowhere.
    broken = MddClient("127.0.0.1", counting_server.port)
    broken._socket = None
    transport._pool.put(broken)
    transport._opened = 1

    with pytest.raises(TransportError):
        transport._execute("cmd")
    assert transport._opened == 0, "the poisoned entry was discarded"

    transport._reset_failure()
    assert transport._execute("cmd") == "ok"
    transport._reset_failure()


def test_pool_is_exhausted_not_unbounded(counting_server):
    """When every slot is checked out the caller gives up instead of
    opening sockets above the cap."""
    transport = RemoteEngineTransport(
        "127.0.0.1", counting_server.port, pool_size=1, command_timeout=10.0
    )
    transport._pool_wait_timeout = 0.2

    client = transport._acquire_client()  # the only connection, held by us
    try:
        started = time.monotonic()
        with pytest.raises(TransportError):
            transport._execute("cmd")
        assert time.monotonic() - started >= 0.2
        assert transport._opened == 1
    finally:
        transport._release_client(client)
    transport._reset_failure()


def test_an_unexpected_error_does_not_leak_a_pool_slot(counting_server):
    """An exception outside the MDD error types must not strand a slot.

    The connection would be neither pooled nor closed, so the pool would
    shrink by one per occurrence until it is permanently exhausted.
    """
    transport = RemoteEngineTransport(
        "127.0.0.1", counting_server.port, pool_size=2, command_timeout=10.0
    )

    class _Boom(Exception):
        pass

    def explode(*_args, **_kwargs):
        raise _Boom("unexpected")

    # Every client the pool hands out must raise, not just one of them.
    original = MddClient.execute_command
    MddClient.execute_command = explode  # type: ignore[method-assign]
    try:
        with pytest.raises(_Boom):
            transport._execute("cmd")
    finally:
        MddClient.execute_command = original  # type: ignore[method-assign]

    assert transport._opened == 0, "the slot must be released, not stranded"
    assert transport._pool.empty()
    transport._reset_failure()
