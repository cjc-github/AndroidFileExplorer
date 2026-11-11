"""API routes for machines (engines) that host ADB servers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BaseResponse
from app.db.session import get_session
from app.logger import logger
from app.models.device import EngineRecord
from app.schemas.device import EngineCreate, EngineInfo
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
    devices = await registry.list_devices()

    counts: dict[int | None, int] = {}
    for device in devices:
        counts[device.engine_id] = counts.get(device.engine_id, 0) + 1

    payload = []
    for engine in engines:
        info = EngineInfo.model_validate(engine)
        info.device_count = counts.get(engine.id, 0)
        payload.append(info)
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
    )
    logger.info(f"Engine created id={engine.id} {engine.host}:{engine.port}")

    try:
        transport = resolver.for_engine(
            engine.id,
            kind=engine.kind,
            host=engine.host,
            port=engine.port,
            token=engine.token,
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

    await registry.update_engine_status(engine.id, "connected")
    resolver.register_engine(
        engine.id, engine.kind, engine.host, engine.port, engine.token
    )
    await db.refresh(engine)
    return BaseResponse.success(
        data=EngineInfo.model_validate(engine),
        message=f"机器已登记，探测到 {len(devices)} 台设备",
    )


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
            engine.id, kind=engine.kind, host=engine.host, port=engine.port
        )
        devices = await transport.list_devices()
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        await registry.update_engine_status(engine.id, "error", str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"连接失败: {exc}"
        ) from exc

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
    logger.info(f"Engine deleted id={engine_id} {engine.host}:{engine.port}")
    return BaseResponse.success(None, message="删除成功")
