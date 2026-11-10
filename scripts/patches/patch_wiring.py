"""One-shot patcher: wire the engine map into startup and fix the local engine lookup.

Two fixes:
1. ``app.main.lifespan`` must teach the resolver about persisted engines.
2. ``app.api.routes.devices._local_engine_id`` called ``db.execute(...)``
   without awaiting it -> coroutine never awaited, engine id was always None.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def patch_main() -> str:
    target = ROOT / "app" / "main.py"
    source = target.read_text(encoding="utf-8")
    if "sync_known_engines" in source:
        return "main already patched"

    old = """    async with async_session_maker() as session:
        registry = DeviceRegistry(session)
        await registry.ensure_local_engine()
    logger.info("Local engine registered")
"""
    new = """    async with async_session_maker() as session:
        registry = DeviceRegistry(session)
        await registry.ensure_local_engine()

    await sync_known_engines()
    logger.info("Local engine registered")
"""
    if old not in source:
        return "main anchor missing"

    source = source.replace(old, new, 1)
    source = source.replace(
        "from app.state import get_transport_resolver",
        "from app.state import get_transport_resolver, sync_known_engines",
        1,
    )
    target.write_text(source, encoding="utf-8")
    return "main patched"


def patch_devices() -> str:
    target = ROOT / "app" / "api" / "routes" / "devices.py"
    source = target.read_text(encoding="utf-8")
    if "await db.execute(" in source:
        return "devices already patched"

    old = """    result = db.execute(
        select(EngineRecord.id).where(
            EngineRecord.kind == "local", EngineRecord.host == "127.0.0.1"
        )
    )
    return result.scalar_one_or_none()
"""
    new = """    result = await db.execute(
        select(EngineRecord.id).where(
            EngineRecord.kind == "local", EngineRecord.host == "127.0.0.1"
        )
    )
    return result.scalar_one_or_none()
"""
    if old not in source:
        return "devices anchor missing"

    source = source.replace(old, new, 1)
    source = source.replace(
        "def _local_engine_id(db: AsyncSession):",
        "async def _local_engine_id(db: AsyncSession):",
        1,
    )
    source = source.replace(
        "await registry.sync_transport(resolver.local, _local_engine_id(db))",
        "await registry.sync_transport(\n            resolver.local, await _local_engine_id(db)\n        )",
        1,
    )
    target.write_text(source, encoding="utf-8")
    return "devices patched"


def main() -> int:
    print(patch_main())
    print(patch_devices())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
