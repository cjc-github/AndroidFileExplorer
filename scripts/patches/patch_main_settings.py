"""One-shot patcher: settings router + the background auto-sync loop."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
MAIN = ROOT / "backend" / "app" / "main.py"
source = MAIN.read_text(encoding="utf-8")
edits = 0


def sub(old: str, new: str, label: str) -> None:
    global source, edits
    if old in source:
        source = source.replace(old, new, 1)
        edits += 1
        print(f"  {label} OK")
    elif new in source:
        print(f"  {label} already applied")
    else:
        print(f"  {label} ANCHOR MISSING")
        raise SystemExit(1)


sub(
    '''from app.api.routes import devices, engines, files''',
    '''from app.api.routes import devices, engines, files, settings''',
    "router import",
)

sub(
    '''    await sync_known_engines()
    logger.info("Local engine registered")

    yield

    await get_transport_resolver().close()
    logger.info("AndroidFileExplorer backend stopped")''',
    '''    await sync_known_engines()
    logger.info("Local engine registered")

    auto_sync_task = asyncio.create_task(_auto_sync_loop())
    logger.info("Auto sync loop started")

    try:
        yield
    finally:
        auto_sync_task.cancel()
        try:
            await auto_sync_task
        except asyncio.CancelledError:
            pass
    await get_transport_resolver().close()
    logger.info("AndroidFileExplorer backend stopped")''',
    "lifespan",
)

sub(
    '''@asynccontextmanager
async def lifespan(app: FastAPI):''',
    '''async def _auto_sync_loop() -> None:
    """Discover devices on a schedule (and run the auto-root hook).

    The interval lives in the platform settings; ``0`` disables the loop, so
    changing the setting takes effect without a restart.  Every failure is
    logged and swallowed: the loop must survive a machine going away.
    """
    from app.db.session import async_session_maker
    from app.api.routes.devices import run_auto_root, _sync_all_machines
    from app.services.settings_service import SettingsService
    from app.services.transport_resolver import TransportResolver

    while True:
        try:
            async with async_session_maker() as session:
                info = await SettingsService(session).read()
                interval = info.auto_sync_seconds
            if interval > 0:
                async with async_session_maker() as session:
                    await _sync_all_machines(session, get_transport_resolver())
                    await run_auto_root(session, get_transport_resolver())
                logger.info(f"auto sync cycle finished (interval {interval}s)")
                await asyncio.sleep(interval)
            else:
                # Disabled: re-check the setting every few seconds so that
                # enabling it takes effect without a restart.
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive anything
            logger.exception("auto sync cycle failed")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):''',
    "loop",
)

sub(
    '''app.include_router(engines.router)''',
    '''app.include_router(engines.router)
app.include_router(settings.router)''',
    "include",
)

MAIN.write_text(source, encoding="utf-8")
print(f"DONE ({edits}/4)")
