"""One-shot patcher: persist the device mutations of the polling path.

`_device_with_metrics` mutates brand / is_rooted / the static properties, but
`list_devices` never committed the session: the values were correct inside
each response yet written to NO database.  After a restart everything fell
back to what the last explicit endpoint had stored.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROUTE = ROOT / "backend" / "app" / "api" / "routes" / "devices.py"
source = ROUTE.read_text(encoding="utf-8")

OLD = '''    devices = await registry.list_devices()
    return BaseResponse.success(
        [
            await _device_with_metrics(device, db, resolver, collector)
            for device in devices
        ]
    )'''

NEW = '''    devices = await registry.list_devices()
    infos = [
        await _device_with_metrics(device, db, resolver, collector)
        for device in devices
    ]
    # The sampling path mutates the records (brand, root flag, static
    # properties); without this commit those values never reach the
    # database and every restart starts from stale rows.
    await db.commit()
    return BaseResponse.success(infos)'''

if OLD in source:
    source = source.replace(OLD, NEW, 1)
    ROUTE.write_text(source, encoding="utf-8")
    print("COMMIT_OK")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)
