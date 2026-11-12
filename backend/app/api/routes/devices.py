"""Device routes: discovery, lifecycle and runtime metrics."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BaseResponse
from app.db.session import get_session
from app.logger import logger
from app.schemas.device import (
    BatchResultItem,
    BatchRootRequest,
    DeviceAliasRequest,
    DeviceInfo,
    DeviceMetrics,
)
from app.services.metrics import MetricsCollector
from app.schemas.settings import SettingsInfo
from app.services.registry import DeviceRegistry
from app.services.settings_service import SettingsService
from app.services.transport_resolver import TransportResolver
from app.state import get_metrics_collector, get_transport_resolver
from app.transports.base import (
    DeviceOfflineError,
    DeviceState,
    DeviceUnauthorizedError,
    PermissionDeniedError,
    TransportError,
)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


def _to_device_info(record, metrics: DeviceMetrics | None = None) -> DeviceInfo:
    info = DeviceInfo.model_validate(record)
    info.metrics = metrics
    return info


def _to_metrics(sample) -> DeviceMetrics:
    return DeviceMetrics(
        cpu_percent=sample.cpu_percent,
        cpu_user_percent=sample.cpu_user_percent,
        cpu_system_percent=sample.cpu_system_percent,
        memory_total_bytes=sample.memory_total_bytes,
        memory_available_bytes=sample.memory_available_bytes,
        memory_used_bytes=sample.memory_used_bytes,
        memory_percent=sample.memory_percent,
        load_average=list(sample.load_average) if sample.load_average else None,
        sampled_at=sample.sampled_at,
    )


#: How many consecutive metric failures flip the device state.  Unauthorized
#: and permission errors are applied immediately; offline / generic errors are
#: debounced because a single miss happens while adbd restarts after root.
METRICS_FAILURE_THRESHOLD = 2


async def _apply_metrics_failure(
    db: AsyncSession, device, collector: MetricsCollector, exc: Exception
) -> None:
    """Reflect a failed sample in the lifecycle column shown by the UI.

    The foreground list polls with ``refresh=false``, so the registry must
    downgrade a device that stopped answering.  Otherwise a removed phone
    stays "online" in the table until the user presses 同步 manually.
    """
    registry = DeviceRegistry(db)
    if isinstance(exc, PermissionDeniedError):
        await registry.mark_status(device, DeviceState.PERMISSION_DENIED.value, str(exc))
    elif isinstance(exc, DeviceUnauthorizedError):
        await registry.mark_status(device, DeviceState.UNAUTHORIZED.value, str(exc))
    else:
        # Offline and generic failures are debounced: a single miss happens
        # while adbd restarts after root/unroot, and flipping the UI then
        # would be misleading.
        streak = collector.record_failure(device.uid)
        if streak >= METRICS_FAILURE_THRESHOLD:
            state = (
                DeviceState.OFFLINE.value
                if isinstance(exc, DeviceOfflineError)
                else DeviceState.ERROR.value
            )
            await registry.mark_status(device, state, str(exc))


async def _local_engine_id(db: AsyncSession) -> int | None:
    """Best effort lookup of the local engine row id."""
    from sqlalchemy import select

    from app.models.device import EngineRecord

    result = await db.execute(
        select(EngineRecord.id).where(
            EngineRecord.kind == "local", EngineRecord.host == "127.0.0.1"
        )
    )
    return result.scalar_one_or_none()


async def run_auto_root(db: AsyncSession, resolver: TransportResolver) -> int:
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


async def _sync_all_machines(
    db: AsyncSession, resolver: TransportResolver
) -> None:
    """Refresh every machine concurrently.

    The machines are independent, so syncing them one after another made the
    request wait for the slowest (or for a dead machine to time out).
    """
    registry = DeviceRegistry(db)
    await registry.ensure_local_engine()

    local_id = await _local_engine_id(db)
    engines = await registry.list_engines()
    remote_engines = [engine for engine in engines if engine.kind != "local"]

    async def _sync_one(transport, engine_id):
        # Every task needs its OWN database session: sharing one session
        # across concurrent tasks makes the commits collide
        # ("commit() can't be called here").
        from app.db.session import async_session_maker

        try:
            async with async_session_maker() as session:
                await DeviceRegistry(session).sync_transport(transport, engine_id)
        except Exception as exc:  # noqa: BLE001 - one machine must not break the rest
            logger.warning(f"sync failed for engine {engine_id}: {exc}")

    tasks = [_sync_one(resolver.local, local_id)]
    for engine in remote_engines:
        transport = resolver.for_engine(engine.id)
        tasks.append(_sync_one(transport, engine.id))

    await asyncio.gather(*tasks, return_exceptions=True)
    await run_auto_root(db, resolver)


@router.get("", response_model=BaseResponse[list[DeviceInfo]], summary="查询设备列表")
async def list_devices(
    refresh: bool = Query(False, description="true 时先从各机器同步一次设备清单"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[list[DeviceInfo]]:
    """List every known device together with its runtime metrics.

    ``refresh=true`` re-syncs the machines first (concurrently). The metrics
    are sampled for every online device; a sample is cached for a short
    window so the polling loop does not hammer the devices.
    """
    registry = DeviceRegistry(db)
    if refresh:
        await _sync_all_machines(db, resolver)

    devices = await registry.list_devices()

    async def _sample_one(device) -> DeviceInfo:
        # Every task gets its OWN database session: the sampling tasks run
        # concurrently, and sharing one session across them makes the
        # writes collide ("commit() can't be called here").
        from app.db.session import async_session_maker

        async with async_session_maker() as session:
            fresh = await DeviceRegistry(session).get_device(device.uid)
            if fresh is None:
                return _to_device_info(device)
            info = await _device_with_metrics(fresh, session, resolver, collector)
            await session.commit()
            return info

    # The devices are independent: sampling them one after another made the
    # request wait for the slowest (or for a dead device to time out).
    infos = list(await asyncio.gather(*(_sample_one(device) for device in devices)))
    return BaseResponse.success(infos)


async def _device_with_metrics(
    device,
    db: AsyncSession,
    resolver: TransportResolver,
    collector: MetricsCollector,
) -> DeviceInfo:
    """Attach a CPU / memory sample to one device (best effort)."""
    if device.status != DeviceState.ONLINE.value:
        return _to_device_info(device)

    try:
        transport = resolver.for_engine(device.engine_id)
        sample = await collector.collect(device.uid, transport, device.serial)
        # The brand is a device property, not a metric: persist it so the
        # device table shows it as well.
        if sample.brand and device.brand != sample.brand:
            device.brand = sample.brand
            device.updated_at = datetime.utcnow()

        # The CPU architecture and the Android version are static device
        # properties too: read them once (the transport caches per device)
        # and persist, so the device table can show them.
        if (
            device.cpu_abi is None
            or device.android_version is None
            or device.board_platform is None
        ):
            props_fetch = getattr(transport, "device_props", None)
            if props_fetch is not None:
                try:
                    props = await props_fetch(device.serial)
                except TransportError:
                    props = {}
                if props:
                    device.cpu_abi = props.get("ro.product.cpu.abi")
                    device.android_version = props.get("ro.build.version.release")
                    device.api_level = props.get("ro.build.version.sdk")
                    device.board_platform = props.get("ro.board.platform")
                    device.updated_at = datetime.utcnow()

        # The CPU hardware facts (core count, max frequency) are static too:
        # read them once (the transport caches per device) and persist.
        if not device.cpu_cores or not device.cpu_max_freq_khz:
            cpu_fetch = getattr(transport, "cpu_hardware_info", None)
            if cpu_fetch is not None:
                try:
                    cores, max_freq = await cpu_fetch(device.serial)
                except TransportError:
                    cores, max_freq = None, None
                # Assign unconditionally: a None result (fake cpufreq on
                # emulators) must OVERWRITE a garbage 0 left by the migration.
                device.cpu_cores = cores
                device.cpu_max_freq_khz = max_freq
                device.updated_at = datetime.utcnow()

        # Whether the device shell runs as root changes only when adbd is
        # restarted, so it is refreshed together with the metrics.
        # Not every transport implements it (a transport may be a duck typed
        # test double), so the call is guarded.
        is_rooted = getattr(transport, "is_rooted", None)
        rooted = await is_rooted(device.serial) if is_rooted else device.is_rooted
        if rooted != device.is_rooted:
            device.is_rooted = rooted
            device.updated_at = datetime.utcnow()
        return _to_device_info(device, _to_metrics(sample))
    except (TransportError, PermissionDeniedError) as exc:
        logger.warning(f"metrics unavailable for {device.uid}: {exc}")
        await _apply_metrics_failure(db, device, collector, exc)
        return _to_device_info(device)


@router.get(
    "/{uid}", response_model=BaseResponse[DeviceInfo], summary="查询设备详情"
)
async def get_device_detail(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[DeviceInfo]:
    """Return one device including a fresh CPU/memory sample."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    metrics: DeviceMetrics | None = None
    if record.status == DeviceState.ONLINE.value:
        try:
            transport = resolver.for_engine(record.engine_id)
            sample = await collector.collect(uid, transport, record.serial, force=True)
            metrics = _to_metrics(sample)
        except (TransportError, PermissionDeniedError) as exc:
            logger.warning(f"metrics failed for {uid}: {exc}")
            await _apply_metrics_failure(db, record, collector, exc)

    return BaseResponse.success(_to_device_info(record, metrics))


@router.get(
    "/{uid}/metrics", response_model=BaseResponse[DeviceMetrics], summary="查询设备运行状态"
)
async def get_device_metrics(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[DeviceMetrics]:
    """Sample CPU / memory of one device. The frontend polls this endpoint."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    if record.status != DeviceState.ONLINE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"设备当前状态为 {record.status}，无法采集指标",
        )

    transport = resolver.for_engine(record.engine_id)
    try:
        sample = await collector.collect(uid, transport, record.serial, force=True)
    except PermissionDeniedError as exc:
        await registry.mark_status(record, DeviceState.PERMISSION_DENIED.value, str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except DeviceUnauthorizedError as exc:
        await registry.mark_status(record, DeviceState.UNAUTHORIZED.value, str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except DeviceOfflineError as exc:
        await registry.mark_status(record, DeviceState.OFFLINE.value, str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return BaseResponse.success(_to_metrics(sample))


@router.patch(
    "/{uid}", response_model=BaseResponse[DeviceInfo], summary="修改设备信息"
)
async def update_device(
    uid: str,
    payload: DeviceAliasRequest,
    db: AsyncSession = Depends(get_session),
) -> BaseResponse[DeviceInfo]:
    """Set the human readable name of a device."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    record.alias = payload.alias.strip() or None
    record.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(record)
    return BaseResponse.success(_to_device_info(record), message="已保存")


@router.post(
    "/batch/root",
    response_model=BaseResponse[list[BatchResultItem]],
    summary="批量切换多台设备的 root 模式",
)
async def batch_root(
    payload: BatchRootRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[list[BatchResultItem]]:
    """Switch the root mode of several devices.

    The devices are handled concurrently: one unreachable machine must not
    delay the others.
    """

    async def handle(uid: str) -> BatchResultItem:
        # Every device gets its own database session: the tasks run
        # concurrently, and sharing one session across them makes the
        # commits collide ("commit() can't be called here").
        from app.db.session import async_session_maker

        try:
            async with async_session_maker() as session:
                registry = DeviceRegistry(session)
                record = await registry.get_device(uid)
                if record is None:
                    return BatchResultItem(uid=uid, ok=False, message="设备不存在")
                transport = resolver.for_engine(record.engine_id)
                ok, message = await transport.set_root(record.serial, payload.enable)
                if not ok:
                    return BatchResultItem(uid=uid, ok=False, message=message)

                collector.reset(uid)
                rooted: bool | None = None
                try:
                    rooted = await transport.is_rooted(record.serial)
                    record.is_rooted = rooted
                    record.last_error = None
                    record.updated_at = datetime.utcnow()
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 - the switch itself worked
                    logger.warning(
                        f"batch root: could not store the state of {uid}: {exc}"
                    )
        except Exception as exc:  # noqa: BLE001 - reported per device
            return BatchResultItem(uid=uid, ok=False, message=str(exc))

        verb = "已以 root 模式重启 adbd" if payload.enable else "已恢复为普通用户模式"
        return BatchResultItem(
            uid=uid, ok=True, message=f"{verb}（root={'是' if rooted else '否'}）", is_rooted=rooted
        )

    results = await asyncio.gather(*(handle(uid) for uid in payload.uids))
    succeeded = sum(1 for item in results if item.ok)
    return BaseResponse.success(
        list(results),
        message=f"批量操作完成：{succeeded}/{len(results)} 台设备成功",
    )


@router.post(
    "/{uid}/root", response_model=BaseResponse[DeviceInfo], summary="尝试以 root 模式重启设备"
)
async def try_root_device(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[DeviceInfo]:
    """Restart adbd with root privileges so protected paths become readable.

    Only userdebug / emulator builds allow this; a production build answers
    "adbd cannot run as root in production builds".
    """
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    transport = resolver.for_engine(record.engine_id)
    ok, message = await transport.set_root(record.serial, True)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    # adbd restarted: the cached metrics belong to the old connection.
    collector.reset(uid)
    rooted = await transport.is_rooted(record.serial)
    record.is_rooted = rooted
    record.last_error = None
    record.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(record)

    return BaseResponse.success(
        _to_device_info(record),
        message=f"已以 root 模式重启 adbd（root={'是' if rooted else '未确认'}）",
    )


@router.post(
    "/{uid}/unroot", response_model=BaseResponse[DeviceInfo], summary="取消 root 模式"
)
async def unroot_device(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[DeviceInfo]:
    """Restart adbd without root privileges again."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    transport = resolver.for_engine(record.engine_id)
    ok, message = await transport.set_root(record.serial, False)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    collector.reset(uid)
    rooted = await transport.is_rooted(record.serial)
    record.is_rooted = rooted
    record.last_error = None
    record.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(record)

    return BaseResponse.success(
        _to_device_info(record),
        message=f"已恢复为普通用户模式（root={'是' if rooted else '否'}）",
    )


@router.post(
    "/{uid}/connect", response_model=BaseResponse[DeviceInfo], summary="连接设备"
)
async def connect_device(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
    collector: MetricsCollector = Depends(get_metrics_collector),
) -> BaseResponse[DeviceInfo]:
    """Reconnect a device and refresh its lifecycle state."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    transport = resolver.for_engine(record.engine_id)
    ok, message = await transport.connect(record.serial)
    if not ok:
        # A failed `adb connect` does NOT prove the device is gone: for a
        # USB / emulator serial the command is simply not applicable
        # ("missing port in specification"). Marking it offline would take a
        # healthy device out of the registry.
        await registry.mark_status(record, DeviceState.ERROR.value, message)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    collector.reset(uid)
    await registry.sync_transport(transport, record.engine_id)
    record = await registry.get_device(uid)
    return BaseResponse.success(_to_device_info(record), message="连接成功")


@router.post(
    "/{uid}/disconnect", response_model=BaseResponse[DeviceInfo], summary="断开设备"
)
async def disconnect_device(
    uid: str,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[DeviceInfo]:
    """Disconnect a device and mark it offline."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")

    transport = resolver.for_engine(record.engine_id)
    ok, message = await transport.disconnect(record.serial)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    await registry.mark_status(record, DeviceState.OFFLINE.value, "已主动断开")
    return BaseResponse.success(_to_device_info(record), message="断开成功")
