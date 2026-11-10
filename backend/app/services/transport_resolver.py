"""Transport factory: maps an engine row onto a live transport instance.

The resolver owns the ``engine_id -> (kind, host, port)`` map so callers only
need the id of a machine. Unknown / missing ids fall back to the local
transport, which is the correct default for a single machine deployment.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import settings
from app.logger import logger
from app.transports.base import Transport
from app.transports.local_adb import LocalAdbTransport
from app.transports.remote_engine import RemoteEngineTransport


class TransportResolver:
    """Builds (and caches) the transport of every known machine."""

    def __init__(self) -> None:
        self._local = LocalAdbTransport(
            adb_path=settings.adb_path,
            server_host=settings.adb_server_host,
            server_port=settings.adb_server_port,
        )
        self._remote: dict[tuple[str, int], RemoteEngineTransport] = {}
        self._engines: dict[int, tuple[str, str, int, str | None]] = {}
        """engine_id -> (kind, host, port)."""
        self._remote_override: "Callable[[str, int], Transport] | None" = None
        """Test hook: replaces the construction of remote transports."""

    @property
    def local(self) -> LocalAdbTransport:
        return self._local

    def register_engine(
        self,
        engine_id: int,
        kind: str,
        host: str,
        port: int,
        token: str | None = None,
    ) -> None:
        """Remember how to reach a machine."""
        self._engines[engine_id] = (kind, host, port, token)

    def for_engine(
        self,
        engine_id: int | None = None,
        kind: str | None = None,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Transport:
        """Return the transport of a machine.

        Resolution order:
        1. explicit ``host``/``port`` (probing a machine that is not persisted yet);
        2. the registered engine map, by ``engine_id`` (the normal path);
        3. the local transport (single machine default).
        """
        if host is None or port is None:
            known = self._engines.get(engine_id) if engine_id is not None else None
            if known is not None:
                kind, host, port, token = known
            elif kind == "remote":
                raise ValueError(f"远程引擎缺少 host/port: engine_id={engine_id}")
            else:
                # Unknown machine -> the local one is the only safe default.
                return self._local

        if host in ("127.0.0.1", "localhost") and port == settings.adb_server_port:
            return self._local

        if self._remote_override is not None:
            return self._remote_override(host, port)

        key = (host, port)
        transport = self._remote.get(key)
        if transport is None:
            transport = RemoteEngineTransport(
                host=host,
                port=port,
                connect_timeout=settings.engine_connect_timeout,
                command_timeout=settings.engine_command_timeout,
                token=token,
            )
            self._remote[key] = transport
            logger.info(f"Created remote transport for {host}:{port}")
        return transport

    async def close(self) -> None:
        for transport in self._remote.values():
            await transport.close()
        self._remote.clear()
