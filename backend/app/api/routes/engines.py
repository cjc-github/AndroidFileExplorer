"""API routes for machines (engines) that host ADB servers."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BaseResponse
from app.db.session import get_session
from app.logger import logger
from app.models.device import EngineRecord
from app.schemas.device import EngineCreate, EngineInfo, EngineUpdate
from app.services.registry import DeviceRegistry
from app.services.transport_resolver import TransportResolver
from app.state import get_transport_resolver

router = APIRouter(prefix="/api/v1/engines", tags=["engines"])


@router.get("", response_model=BaseResponse[list[EngineInfo]], summary="查询机器列表")
async def list_engines(
    db: AsyncSession = Depends(get_session),
) -> BaseResponse[list[EngineInfo]]:
    """List every machine that can host Android devices."""
    registry = DeviceRegistry(db)
    engines = await registry.list_engines()

    # The count is what each engine's LAST probe/sync reported (see
    # EngineRecord.device_count) - counting attributed rows would be zero-sum
    # when several engines see the same physical device.
    payload = [EngineInfo.model_validate(engine) for engine in engines]
    return BaseResponse.success(payload)


@router.post("", response_model=BaseResponse[EngineInfo], summary="新增机器")
async def create_engine(
    payload: EngineCreate,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[EngineInfo]:
    """Register a remote machine and probe whether its ADB server answers."""
    registry = DeviceRegistry(db)
    duplicate = await db.execute(
        select(EngineRecord).where(
            EngineRecord.kind == payload.kind,
            EngineRecord.host == payload.host,
            EngineRecord.port == payload.port,
        )
    )
    if duplicate.scalars().first() is not None:
        # Two rows for one machine would also mean two transports for the
        # same agent; the registry is keyed by machine, so refuse it early.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该机器已登记: {payload.host}:{payload.port}（{payload.kind}）",
        )

    engine = await registry.create_engine(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        kind=payload.kind,
        token=payload.token,
        ssh_username=payload.ssh_username,
        ssh_password=payload.ssh_password,
        ssh_port=payload.ssh_port,
        adb_path=payload.adb_path,
    )
    logger.info(f"Engine created id={engine.id} {engine.host}:{engine.port}")

    try:
        transport = resolver.for_engine(
            engine.id,
            kind=engine.kind,
            host=engine.host,
            port=engine.port,
            token=engine.token,
            ssh_username=engine.ssh_username,
            ssh_password=engine.ssh_password,
            ssh_port=engine.ssh_port,
            adb_path=engine.adb_path,
        )
        devices = await transport.list_devices()
    except Exception as exc:  # noqa: BLE001 - the failure is reported to the caller
        logger.warning(f"Engine probe failed for {engine.host}:{engine.port}: {exc}")
        await registry.update_engine_status(engine.id, "error", str(exc))
        await db.refresh(engine)
        return BaseResponse.success(
            data=EngineInfo.model_validate(engine),
            message=f"机器已登记，但探测失败: {exc}",
        )

    engine.device_count = len(devices)
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
    )


@router.patch(
    "/{engine_id}",
    response_model=BaseResponse[EngineInfo],
    summary="修改机器配置",
)
async def update_engine(
    engine_id: int,
    payload: EngineUpdate,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[EngineInfo]:
    """Edit a registered machine without deleting its device rows.

    Blank token / SSH password means "keep the stored value", because the
    edit form never receives the secret back from the API.
    """
    registry = DeviceRegistry(db)
    engine = await registry.get_engine(engine_id)
    if engine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="机器不存在")

    new_host = payload.host if payload.host is not None else engine.host
    ssh_port_update = (
        payload.ssh_port if payload.ssh_port is not None else payload.port
    ) if engine.kind == "ssh" else None
    new_port = (
        ssh_port_update
        if ssh_port_update is not None
        else (payload.port if payload.port is not None else engine.port)
    )

    duplicate = await db.execute(
        select(EngineRecord).where(
            EngineRecord.kind == engine.kind,
            EngineRecord.host == new_host,
            EngineRecord.port == new_port,
            EngineRecord.id != engine_id,
        )
    )
    if duplicate.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该机器已登记: {new_host}:{new_port}（{engine.kind}）",
        )

    if payload.name is not None:
        engine.name = payload.name.strip() or f"{new_host}:{new_port}"
    if payload.host is not None:
        engine.host = payload.host
    if engine.kind == "ssh" and ssh_port_update is not None:
        engine.ssh_port = ssh_port_update
        engine.port = ssh_port_update
    elif payload.port is not None:
        engine.port = payload.port
    if payload.token is not None and payload.token.strip():
        engine.token = payload.token.strip()
    if payload.ssh_username is not None and payload.ssh_username.strip():
        engine.ssh_username = payload.ssh_username.strip()
    if payload.ssh_password is not None and payload.ssh_password.strip():
        engine.ssh_password = payload.ssh_password.strip()
    if payload.adb_path is not None:
        engine.adb_path = payload.adb_path.strip() or None

    engine.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(engine)

    # Only discard the old transport after the row is safely stored: if the
    # commit failed, the resolver must keep its previous registration.
    await resolver.drop_engine(engine_id)
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

    message = "机器配置已保存"
    try:
        devices = await resolver.for_engine(engine.id).list_devices()
    except Exception as exc:  # noqa: BLE001 - the config is saved, probing is best effort
        logger.warning(f"Engine probe failed after update {engine.host}:{engine.port}: {exc}")
        await registry.update_engine_status(engine.id, "error", str(exc))
        await db.refresh(engine)
        message = f"机器配置已保存，但探测失败: {exc}"
    else:
        engine.device_count = len(devices)
        await registry.update_engine_status(engine.id, "connected")
        await db.refresh(engine)
        message = f"机器配置已保存，探测到 {len(devices)} 台设备"

    return BaseResponse.success(EngineInfo.model_validate(engine), message=message)


@router.post(
    "/{engine_id}/test",
    response_model=BaseResponse[dict],
    summary="测试机器连接",
)
async def test_engine(
    engine_id: int,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Probe a machine and report how many devices it sees."""
    registry = DeviceRegistry(db)
    engine = await registry.get_engine(engine_id)
    if engine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="机器不存在")

    try:
        transport = resolver.for_engine(
            engine.id,
            kind=engine.kind,
            host=engine.host,
            port=engine.port,
            token=engine.token,
            ssh_username=engine.ssh_username,
            ssh_password=engine.ssh_password,
            ssh_port=engine.ssh_port,
            adb_path=engine.adb_path,
        )
        devices = await transport.list_devices()
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        await registry.update_engine_status(engine.id, "error", str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"连接失败: {exc}"
        ) from exc

    engine.device_count = len(devices)
    await registry.update_engine_status(engine.id, "connected")
    await db.refresh(engine)
    return BaseResponse.success(
        {
            "engine_id": engine.id,
            "status": "connected",
            "device_count": len(devices),
        },
        message=f"连接成功，探测到 {len(devices)} 台设备",
    )


@router.delete("/{engine_id}", response_model=BaseResponse[None], summary="删除机器")
async def delete_engine(
    engine_id: int,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[None]:
    """Remove a machine and its device rows.

    The local machine cannot be removed: it is the transport this very API
    server uses.
    """
    registry = DeviceRegistry(db)
    engine = await registry.get_engine(engine_id)
    if engine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="机器不存在")
    if engine.kind == "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="本机不可删除"
        )

    removed = await registry.delete_engine(engine_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="机器不存在")
    await resolver.drop_engine(engine_id)
    logger.info(f"Engine deleted id={engine_id} {engine.host}:{engine.port}")
    return BaseResponse.success(None, message="删除成功")
