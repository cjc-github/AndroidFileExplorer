"""One-shot patcher: make the engine device_count match what a probe sees.

The device table's count was "how many device rows are attributed to this
engine", but device identity is engine-independent (uid = hw serial): the
rows follow the LAST engine that synced them, so with two engines seeing the
same phone the attribution was zero-sum (agent: 0, ssh: 1) while both probes
report 1.

Fix: store the LIVE probe result per engine (`engines.device_count`), written
at the three probe sites (create / test / sync). The list reads the column.
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
            '''    adb_path: str | None = Field(default=None, max_length=300)
    """adb binary on the remote machine when it is not in PATH."""''',
            '''    adb_path: str | None = Field(default=None, max_length=300)
    """adb binary on the remote machine when it is not in PATH."""
    device_count: int = Field(default=0)
    """Devices seen by the LAST probe/sync of this machine.

    Device identity is engine-independent (uid = hw serial), so counting the
    device rows per engine is zero-sum when several engines see the same
    phone; this column keeps what the engine ACTUALLY reported last time.
    """''',
            "column",
        ),
    ],
    "model",
)

# ---------------------------------------------------------------- registry
patch(
    BE / "app" / "services" / "registry.py",
    [
        (
            '''        seen_uids: set[str] = set()
        for entry in entries:
            record = await self.upsert_device(transport, entry, engine_id)
            seen_uids.add(record.uid)''',
            '''        seen_uids: set[str] = set()
        for entry in entries:
            record = await self.upsert_device(transport, entry, engine_id)
            seen_uids.add(record.uid)

        # Remember what THIS machine reported, independent of where the device
        # rows end up being attributed.
        if engine_id is not None:
            engine = await self.db.get(EngineRecord, engine_id)
            if engine is not None and engine.device_count != len(entries):
                engine.device_count = len(entries)
                engine.updated_at = datetime.now(timezone.utc)''',
            "sync writes count",
        ),
    ],
    "registry",
)

# ---------------------------------------------------------------- engines route
patch(
    BE / "app" / "api" / "routes" / "engines.py",
    [
        (
            '''    registry = DeviceRegistry(db)
    engines = await registry.list_engines()
    devices = await registry.list_devices()

    counts: dict[int | None, int] = {}
    for device in devices:
        counts[device.engine_id] = counts.get(device.engine_id, 0) + 1

    payload = []
    for engine in engines:
        info = EngineInfo.model_validate(engine)
        info.device_count = counts.get(engine.id, 0)
        payload.append(info)
    return BaseResponse.success(payload)''',
            '''    registry = DeviceRegistry(db)
    engines = await registry.list_engines()

    # The count is what each engine's LAST probe/sync reported (see
    # EngineRecord.device_count) - counting attributed rows would be zero-sum
    # when several engines see the same physical device.
    payload = [EngineInfo.model_validate(engine) for engine in engines]
    return BaseResponse.success(payload)''',
            "list reads column",
        ),
        (
            '''    await registry.update_engine_status(engine.id, "connected")
    resolver.register_engine(
        engine.id,
        engine.kind,
        engine.host,
        engine.port,
        engine.token,
        engine.ssh_username,
        engine.ssh_password,
        engine.ssh_port,
        engine.adb_path,
    )
    await db.refresh(engine)
    return BaseResponse.success(
        data=EngineInfo.model_validate(engine),
        message=f"机器已登记，探测到 {len(devices)} 台设备",
    )''',
            '''    engine.device_count = len(devices)
    await registry.update_engine_status(engine.id, "connected")
    resolver.register_engine(
        engine.id,
        engine.kind,
        engine.host,
        engine.port,
        engine.token,
        engine.ssh_username,
        engine.ssh_password,
        engine.ssh_port,
        engine.adb_path,
    )
    await db.refresh(engine)
    return BaseResponse.success(
        data=EngineInfo.model_validate(engine),
        message=f"机器已登记，探测到 {len(devices)} 台设备",
    )''',
            "create writes count",
        ),
    ],
    "engines",
)

# test route: persist the probe count as well
source = (BE / "app" / "api" / "routes" / "engines.py").read_text(encoding="utf-8")
OLD_TEST = '''        devices = await transport.list_devices()
    except Exception as exc:  # noqa: BLE001 - the failure is reported to the caller
        logger.warning(f"Engine probe failed for {engine.host}:{engine.port}: {exc}")
        await registry.update_engine_status(engine.id, "error", str(exc))
        await db.refresh(engine)
        return BaseResponse.success(
            data=EngineInfo.model_validate(engine),
            message=f"机器已登记，但探测失败: {exc}",
        )'''
if OLD_TEST in source:
    print("  (that block belongs to create - skipping)")
# the test route marks connected then returns - insert the count there
OLD_TEST2 = '''    await registry.update_engine_status(engine.id, "connected")
    await db.refresh(engine)
    return BaseResponse.success(
        data=EngineInfo.model_validate(engine),
        message=f"连接成功，探测到 {len(devices)} 台设备",
    )'''
NEW_TEST2 = '''    engine.device_count = len(devices)
    await registry.update_engine_status(engine.id, "connected")
    await db.refresh(engine)
    return BaseResponse.success(
        data=EngineInfo.model_validate(engine),
        message=f"连接成功，探测到 {len(devices)} 台设备",
    )'''
if OLD_TEST2 in source:
    source = source.replace(OLD_TEST2, NEW_TEST2, 1)
    edits += 1
    print("  test route count OK")
elif NEW_TEST2 in source:
    print("  test route count already applied")
else:
    print("  test route ANCHOR MISSING")
    raise SystemExit(1)
(BE / "app" / "api" / "routes" / "engines.py").write_text(source, encoding="utf-8")

print(f"DONE ({edits})")
