"""One-shot patcher: wire the SSH transport into the platform.

Model columns (auto-migrated), schema fields (password never echoed back),
resolver branch for kind="ssh", registry/routes plumbing.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BE = ROOT / "backend"

edits = 0


def patch(path: pathlib.Path, pairs, label: str) -> None:
    global edits
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            edits += 1
            print(f"  {label}.{name} OK")
        elif new in source:
            print(f"  {label}.{name} already applied")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---------------------------------------------------------------- model
patch(
    BE / "app" / "models" / "device.py",
    [
        (
            '''    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""''',
            '''    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""

    ssh_username: str | None = Field(default=None, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=200)
    """SSH credentials for kind="ssh" machines (never echoed by the API)."""
    ssh_port: int = Field(default=22)
    adb_path: str | None = Field(default=None, max_length=300)
    """adb binary on the remote machine when it is not in PATH."""''',
            "columns",
        ),
    ],
    "model",
)

# ---------------------------------------------------------------- schemas
patch(
    BE / "app" / "schemas" / "device.py",
    [
        (
            '''    kind: str = Field(default="remote", pattern="^(local|remote)$")
    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""''',
            '''    kind: str = Field(default="remote", pattern="^(local|remote|ssh)$")
    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""
    ssh_username: str | None = Field(default=None, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=200)
    """SSH credentials for kind="ssh" machines."""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    adb_path: str | None = Field(default=None, max_length=300)''',
            "create",
        ),
    ],
    "schema-create",
)

# EngineInfo: expose ssh fields but never the password
patch(
    BE / "app" / "schemas" / "device.py",
    [
        (
            '''class EngineInfo(BaseModel):
    """View model of a machine that hosts ADB."""

    id: int''',
            '''class EngineInfo(BaseModel):
    """View model of a machine that hosts ADB."""

    id: int
    ssh_username: str | None = None
    ssh_port: int = 22
    has_ssh_password: bool = False
    """True when an SSH password is stored; the value itself never leaves."""
    adb_path: str | None = None''',
            "info",
        ),
    ],
    "schema-info",
)

# ---------------------------------------------------------------- resolver
patch(
    BE / "app" / "services" / "transport_resolver.py",
    [
        (
            '''from app.logger import logger''',
            '''from dataclasses import dataclass

from app.logger import logger''',
            "dataclass import",
        ),
        (
            '''        self._engines: dict[int, tuple[str, str, int, str | None]] = {}''',
            '''        self._engines: dict[int, "EngineEndpoint"] = {}
        self._ssh_transports: dict[tuple[str, int, str], Transport] = {}''',
            "caches",
        ),
        (
            '''    def register_engine(
        self,
        engine_id: int,
        kind: str,
        host: str,
        port: int,
        token: str | None = None,
    ) -> None:
        """Remember how to reach a machine."""
        self._engines[engine_id] = (kind, host, port, token)''',
            '''    def register_engine(
        self,
        engine_id: int,
        kind: str,
        host: str,
        port: int,
        token: str | None = None,
        ssh_username: str | None = None,
        ssh_password: str | None = None,
        ssh_port: int = 22,
        adb_path: str | None = None,
    ) -> None:
        """Remember how to reach a machine."""
        self._engines[engine_id] = EngineEndpoint(
            kind=kind,
            host=host,
            port=port,
            token=token,
            ssh_username=ssh_username,
            ssh_password=ssh_password,
            ssh_port=ssh_port,
            adb_path=adb_path,
        )''',
            "register",
        ),
        (
            '''            known = self._engines.get(engine_id) if engine_id is not None else None
            if known is not None:
                kind, host, port, token = known''',
            '''            known = self._engines.get(engine_id) if engine_id is not None else None
            if known is not None:
                kind = known.kind
                host = known.host
                port = known.port
                token = known.token
                ssh_username = known.ssh_username
                ssh_password = known.ssh_password
                ssh_port = known.ssh_port
                adb_path = known.adb_path''',
            "lookup",
        ),
        (
            '''        if host in ("127.0.0.1", "localhost") and port == settings.adb_server_port:
            return self._local''',
            '''        if (
            kind != "ssh"
            and host in ("127.0.0.1", "localhost")
            and port == settings.adb_server_port
        ):
            return self._local''',
            "local shortcut guard",
        ),
        (
            '''        key = (host, port)
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
        return transport''',
            '''        if kind == "ssh":
            ssh_key = (host, ssh_port, ssh_username or "root")
            transport = self._ssh_transports.get(ssh_key)
            if transport is None:
                transport = SshAdbTransport(
                    host=host,
                    ssh_port=ssh_port,
                    username=ssh_username or "root",
                    password=ssh_password,
                    adb_path=adb_path or "adb",
                    connect_timeout=settings.engine_connect_timeout,
                    command_timeout=settings.engine_command_timeout,
                )
                self._ssh_transports[ssh_key] = transport
                logger.info(f"Created SSH transport for {ssh_username}@{host}:{ssh_port}")
            return transport

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
        return transport''',
            "ssh branch",
        ),
        (
            '''    async def close(self) -> None:
        for transport in self._remote.values():
            await transport.close()
        self._remote.clear()''',
            '''    async def close(self) -> None:
        for transport in self._remote.values():
            await transport.close()
        self._remote.clear()
        for transport in self._ssh_transports.values():
            await transport.close()
        self._ssh_transports.clear()''',
            "close",
        ),
    ],
    "resolver",
)

print(f"DONE ({edits})")
