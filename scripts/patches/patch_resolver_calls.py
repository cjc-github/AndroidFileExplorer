"""One-shot patcher: resolve the transport of a device by its engine id.

The routes used to call ``resolver.for_engine(engine_id, kind=record.transport_kind)``.
``transport_kind`` describes the DEVICE (``local`` / ``remote``), not the
machine, and it did not carry host/port, so the resolver could not build the
right transport.

The patch replaces those calls with a lookup by ``engine_id`` only, which is
the key the resolver actually owns.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent

REPLACEMENTS = [
    (
        ROOT / "app" / "api" / "routes" / "devices.py",
        "transport = resolver.for_engine(record.engine_id, kind=record.transport_kind)",
        "transport = resolver.for_engine(record.engine_id)",
    ),
    (
        ROOT / "app" / "api" / "routes" / "files.py",
        "transport = resolver.for_engine(record.engine_id, kind=record.transport_kind)",
        "transport = resolver.for_engine(record.engine_id)",
    ),
]


def main() -> int:
    for target, old, new in REPLACEMENTS:
        source = target.read_text(encoding="utf-8")
        if old not in source:
            print(f"{target.name}: anchor missing")
            continue
        count = source.count(old)
        source = source.replace(old, new)
        target.write_text(source, encoding="utf-8")
        print(f"{target.name}: patched {count} call site(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
