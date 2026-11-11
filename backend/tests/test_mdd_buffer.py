"""The MddClient protocol reader must survive coalesced TCP chunks.

A real server writes header lines, file payloads and markers with separate
send() calls, but TCP is free to deliver them coalesced in one recv(). The
buffered reader has to hand exactly the right bytes to each protocol step
and keep anything it read ahead of time for the next read.
"""

from __future__ import annotations

import io

import pytest

from app.transports.mdd_client import (
    END_MARKER,
    MddClient,
    MddConnectionError,
)


class _FakeSock:
    """A socket-shaped object serving a fixed byte stream."""

    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.recv_sizes: list[int] = []

    def recv(self, size: int) -> bytes:
        self.recv_sizes.append(size)
        return self._stream.read(size)


def _client_with(payload: bytes) -> tuple[MddClient, _FakeSock]:
    client = MddClient("127.0.0.1", 12345)
    sock = _FakeSock(payload)
    client._socket = sock  # type: ignore[assignment]
    return client, sock


def test_lines_are_read_from_a_single_coalesced_chunk():
    client, sock = _client_with(b"FILE_INFO:FILE:a.txt\nFILE_DATA:a.txt:5:md5\n")
    assert client._read_line() == "FILE_INFO:FILE:a.txt"
    assert client._read_line() == "FILE_DATA:a.txt:5:md5"
    assert sock.recv_sizes == [8192], "one buffered recv, not one per byte"


def test_read_exact_consumes_payload_and_keeps_the_leftover():
    client, _sock = _client_with(b"header\n" + b"AB" * 10 + b"TRAILING")
    assert client._read_line() == "header"
    assert client._read_exact(20) == b"AB" * 10
    assert client._recv_buffer == b"TRAILING"


def test_read_exact_needs_multiple_chunks():
    client, _sock = _client_with(b"12345678")
    assert client._read_exact(8) == b"12345678"


def test_read_until_end_keeps_data_after_the_marker():
    payload = (f"first\n{END_MARKER}\nTAIL").encode()
    client, _sock = _client_with(payload)
    assert client._read_until_end() == "first"
    assert client._recv_buffer == b"\nTAIL"


def test_line_reader_handles_split_chunks():
    """A line may arrive in pieces; the reader assembles it."""
    client = MddClient("127.0.0.1", 12345)
    client._recv_buffer = bytearray(b"half ")

    class _DripSock:
        def __init__(self, pieces):
            self._pieces = list(pieces)

        def recv(self, _size: int) -> bytes:
            return self._pieces.pop(0) if self._pieces else b""

    client._socket = _DripSock([b"li", b"ne\n"])  # type: ignore[assignment]
    assert client._read_line() == "half line"


def test_empty_stream_is_a_connection_error():
    client, _sock = _client_with(b"")
    with pytest.raises(MddConnectionError):
        client._read_line()
