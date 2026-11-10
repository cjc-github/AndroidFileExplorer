"""Regression tests for the agent authentication.

The agent executes adb commands for whoever connects.  When a token is
configured, the client must send ``AUTH <token>`` as the first line - and a
client without the secret must not get a single command executed.
"""

from __future__ import annotations

import socket
import threading

import pytest

from tests.test_agent import agent


class _FakeHandler(agent.AgentHandler):
    """Reuse the real handshake logic with a stubbed command runner."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    def run_stub(self, line: str) -> str:  # pragma: no cover - replaced below
        raise NotImplementedError


def _make_handler(token: str | None):
    handler = _FakeHandler(token)

    class _Reader:
        def __init__(self, lines: list[bytes]) -> None:
            self._lines = list(lines)

        def readline(self) -> bytes:
            return self._lines.pop(0) if self._lines else b""

    handler.rfile = _Reader([])
    sent: list[bytes] = []

    class _Writer:
        def write(self, data: bytes) -> None:
            sent.append(data)

        def flush(self) -> None:
            return None

    handler.wfile = _Writer()
    handler._sent = sent
    return handler


def test_no_token_configured_skips_the_handshake():
    handler = _make_handler(None)
    assert handler._authenticate() is True


def test_a_wrong_token_is_rejected():
    handler = _make_handler("secret")
    handler.rfile._lines = [b"AUTH wrong\n"]
    assert handler._authenticate() is False
    assert b"authentication failed" in b"".join(handler._sent)


def test_a_non_auth_first_line_is_rejected():
    handler = _make_handler("secret")
    handler.rfile._lines = [b"adb devices\n"]
    assert handler._authenticate() is False


def test_the_correct_token_is_accepted():
    handler = _make_handler("secret")
    handler.rfile._lines = [b"AUTH secret\n"]
    assert handler._authenticate() is True
    assert b"".join(handler._sent).startswith(b"OK")


def test_an_empty_connection_is_rejected():
    handler = _make_handler("secret")
    handler.rfile._lines = [b""]
    assert handler._authenticate() is False


def _echo_agent_server(token: str | None) -> tuple[socket.socket, threading.Thread]:
    """A minimal socket server that speaks the agent handshake."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve() -> None:
        connection, _ = server.accept()
        with connection:
            if token:
                line = connection.recv(256).decode().strip()
                keyword, _, value = line.partition(" ")
                if keyword.upper() != "AUTH" or value != token:
                    connection.sendall(b"ERROR: authentication failed\n")
                    return
                connection.sendall(b"OK\n")
            data = connection.recv(256)
            connection.sendall(data.upper() + b"\n")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread


def test_mdd_client_authenticates_with_the_token():
    from app.transports.mdd_client import MddClient

    server, thread = _echo_agent_server("secret")
    port = server.getsockname()[1]
    client = MddClient("127.0.0.1", port, connect_timeout=5, command_timeout=5, token="secret")
    client.connect()
    client.close()
    thread.join(timeout=5)
    server.close()


def test_mdd_client_without_a_token_is_refused():
    """The rejection surfaces on the FIRST command: the server closes the
    connection because the client never authenticated."""
    from app.transports.mdd_client import MddClient, MddError

    server, thread = _echo_agent_server("secret")
    port = server.getsockname()[1]
    client = MddClient("127.0.0.1", port, connect_timeout=5, command_timeout=5)
    client.connect()  # the handshake is skipped without a token
    with pytest.raises(MddError):
        client.execute_command("adb devices", timeout=5)
    client.close()
    thread.join(timeout=5)
    server.close()
