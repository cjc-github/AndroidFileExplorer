"""One-shot patcher v2: EngineEndpoint dataclass, for_engine params, plumbing."""

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


# ---------------------------------------------------------------- resolver
RESOLVER = BE / "app" / "services" / "transport_resolver.py"

# where is the class defined? find the class TransportResolver line
resolver_source = RESOLVER.read_text(encoding="utf-8")
has_dataclass = "class EngineEndpoint" in resolver_source
print(f"  EngineEndpoint present: {has_dataclass}")

if not has_dataclass:
    patch(
        RESOLVER,
        [
            (
                '''from dataclasses import dataclass''',
                '''from dataclasses import dataclass''',
                "noop",
            ),
        ],
        "resolver",
    )
    # insert the dataclass right before the resolver class
    source = RESOLVER.read_text(encoding="utf-8")
    anchor = "class TransportResolver:"
    definition = '''@dataclass(frozen=True)
class EngineEndpoint:
    """How to reach one registered machine."""

    kind: str
    host: str
    port: int
    token: str | None = None
    ssh_username: str | None = None
    ssh_password: str | None = None
    ssh_port: int = 22
    adb_path: str | None = None


class TransportResolver:'''
    if anchor in source:
        source = source.replace(anchor, definition, 1)
        edits += 1
        print("  resolver.EngineEndpoint OK")
        RESOLVER.write_text(source, encoding="utf-8")
    else:
        print("  resolver.EngineEndpoint ANCHOR MISSING")
        raise SystemExit(1)

# for_engine signature + the SshAdbTransport import
patch(
    RESOLVER,
    [
        (
            '''        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> Transport:''',
            '''        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        ssh_username: str | None = None,
        ssh_password: str | None = None,
        ssh_port: int = 22,
        adb_path: str | None = None,
    ) -> Transport:''',
            "for_engine signature",
        ),
    ],
    "resolver-signature",
)

resolver_source = RESOLVER.read_text(encoding="utf-8")
if "SshAdbTransport" not in resolver_source:
    patch(
        RESOLVER,
        [
            (
                '''from app.transports.remote_engine import RemoteEngineTransport''',
                '''from app.transports.remote_engine import RemoteEngineTransport
from app.transports.ssh_adb import SshAdbTransport''',
                "ssh import",
            ),
        ],
        "resolver",
    )
else:
    print("  resolver.ssh import already applied")

# ---------------------------------------------------------------- registry
patch(
    BE / "app" / "services" / "registry.py",
    [
        (
            '''        kind: str = "remote",
        token: str | None = None,
    ) -> EngineRecord:
        engine = EngineRecord(
            name=name or f"{host}:{port}",
            host=host,
            port=port,
            kind=kind,
            token=token or None,
            status="unknown",
        )''',
            '''        kind: str = "remote",
        token: str | None = None,
        ssh_username: str | None = None,
        ssh_password: str | None = None,
        ssh_port: int = 22,
        adb_path: str | None = None,
    ) -> EngineRecord:
        engine = EngineRecord(
            name=name or f"{host}:{port}",
            host=host,
            port=port,
            kind=kind,
            token=token or None,
            ssh_username=ssh_username or None,
            ssh_password=ssh_password or None,
            ssh_port=ssh_port,
            adb_path=adb_path or None,
            status="unknown",
        )''',
            "create_engine",
        ),
    ],
    "registry",
)

# ---------------------------------------------------------------- engines route
patch(
    BE / "app" / "api" / "routes" / "engines.py",
    [
        (
            '''    engine = await registry.create_engine(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        kind=payload.kind,
        token=payload.token,
    )''',
            '''    engine = await registry.create_engine(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        kind=payload.kind,
        token=payload.token,
        ssh_username=payload.ssh_username,
        ssh_password=payload.ssh_password,
        ssh_port=payload.ssh_port,
        adb_path=payload.adb_path,
    )''',
            "create call",
        ),
        (
            '''        transport = resolver.for_engine(
            engine.id,
            kind=engine.kind,
            host=engine.host,
            port=engine.port,
            token=engine.token,
        )''',
            '''        transport = resolver.for_engine(
            engine.id,
            kind=engine.kind,
            host=engine.host,
            port=engine.port,
            token=engine.token,
            ssh_username=engine.ssh_username,
            ssh_password=engine.ssh_password,
            ssh_port=engine.ssh_port,
            adb_path=engine.adb_path,
        )''',
            "probe",
        ),
        (
            '''    resolver.register_engine(
        engine.id, engine.kind, engine.host, engine.port, engine.token
    )''',
            '''    resolver.register_engine(
        engine.id,
        engine.kind,
        engine.host,
        engine.port,
        engine.token,
        engine.ssh_username,
        engine.ssh_password,
        engine.ssh_port,
        engine.adb_path,
    )''',
            "register",
        ),
    ],
    "engines",
)

# ---------------------------------------------------------------- state
patch(
    BE / "app" / "state.py",
    [
        (
            '''            _transport_resolver.register_engine(
                engine.id, engine.kind, engine.host, engine.port, engine.token
            )''',
            '''            _transport_resolver.register_engine(
                engine.id,
                engine.kind,
                engine.host,
                engine.port,
                engine.token,
                engine.ssh_username,
                engine.ssh_password,
                engine.ssh_port,
                engine.adb_path,
            )''',
            "startup",
        ),
    ],
    "state",
)

print(f"DONE ({edits})")
