"""One-shot patcher: auto-root after a sync + the shared helper."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROUTE = ROOT / "backend" / "app" / "api" / "routes" / "devices.py"
source = ROUTE.read_text(encoding="utf-8")
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


# imports: SettingsService + logger already? logger exists. Add service import.
sub(
    '''from app.services.registry import DeviceRegistry''',
    '''from app.schemas.settings import SettingsInfo
from app.services.registry import DeviceRegistry
from app.services.settings_service import SettingsService''',
    "imports",
)

# the helper, placed right before _sync_all_machines
sub(
    '''async def _sync_all_machines(''',
    '''async def run_auto_root(db: AsyncSession, resolver: TransportResolver) -> int:
    """Try to root every online device that is not rooted yet.

    Enabled by the ``auto_root`` platform setting; a production device simply
    logs a refusal instead of turning into an error.
    """
    settings_info: SettingsInfo = await SettingsService(db).read()
    if not settings_info.auto_root:
        return 0

    registry = DeviceRegistry(db)
    devices = await registry.list_devices()
    targets = [
        device
        for device in devices
        if device.status == DeviceState.ONLINE.value and not device.is_rooted
    ]
    if not targets:
        return 0

    rooted = 0

    async def root_one(device) -> None:
        nonlocal rooted
        try:
            transport = resolver.for_engine(device.engine_id)
            ok, message = await transport.set_root(device.serial, True)
        except Exception as exc:  # noqa: BLE001 - one device must not break the rest
            logger.warning(f"auto root failed for {device.uid}: {exc}")
            return
        if ok:
            device.is_rooted = True
            device.last_error = None
            device.updated_at = datetime.utcnow()
            rooted += 1
            logger.info(f"auto root succeeded for {device.uid} ({device.serial})")
        else:
            logger.info(f"auto root refused for {device.uid}: {message}")

    await asyncio.gather(*(root_one(device) for device in targets))
    await db.commit()
    return rooted


async def _sync_all_machines(''',
    "helper",
)

# call it at the end of a sync
sub(
    '''    await asyncio.gather(*tasks, return_exceptions=True)''',
    '''    await asyncio.gather(*tasks, return_exceptions=True)
    await run_auto_root(db, resolver)''',
    "sync hook",
)

ROUTE.write_text(source, encoding="utf-8")
print(f"DONE ({edits}/3)")
