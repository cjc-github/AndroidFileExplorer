"""Device registry: stable unique id + lifecycle state machine.

Every device gets one stable id derived from ``(transport kind, machine
endpoint, serial)``. The id is what the frontend uses for every follow-up
call (status, reconnect, file browsing, transfer), so it survives reconnects.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import logger
from app.models.device import DeviceRecord, EngineRecord
from app.transports.base import (
    DeviceEntry,
    DeviceOfflineError,
    DeviceState,
    DeviceUnauthorizedError,
    PermissionDeniedError,
    Transport,
    TransportError,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_device_uid(
    transport_kind: str,
    endpoint: str,
    serial: str,
    hardware_id: str | None = None,
) -> str:
    """Derive the stable unique id of a device.

    The id is based on the device's OWN serial number (``ro.serialno``) when
    the device reports one: the same phone behind two different agents is
    still the same phone and must not show up twice.

    Devices that do not report a hardware serial (and the emulator serials
    that are reused across machines) fall back to the machine plus serial,
    which keeps the id stable across reconnects for that machine.
    """
    identity = hardware_id.strip() if hardware_id and hardware_id.strip() else None
    if identity:
        raw = f"hw|{identity}".encode()
    else:
        raw = f"{transport_kind}|{endpoint}|{serial}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


@dataclass(slots=True)
class DeviceContext:
    """Everything a request needs to talk to one device."""

    uid: str
    record: DeviceRecord
    transport: Transport

    @property
    def serial(self) -> str:
        return self.record.serial

    @property
    def status(self) -> str:
        return self.record.status


class DeviceRegistry:
    """Keeps the device table in sync with the machines and owns the state machine."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------ engines

    async def ensure_local_engine(self) -> EngineRecord:
        """Return (creating if needed) the engine row for this very host."""
        result = await self.db.execute(
            select(EngineRecord).where(
                EngineRecord.kind == "local", EngineRecord.host == "127.0.0.1"
            )
        )
        engine = result.scalars().first()
        if engine is not None:
            return engine
        engine = EngineRecord(
            name="本机",
            host="127.0.0.1",
            port=5037,
            kind="local",
            status="connected",
            last_seen=_utcnow(),
        )
        self.db.add(engine)
        await self.db.commit()
        await self.db.refresh(engine)
        logger.info(f"Created local engine row id={engine.id}")
        return engine

    async def list_engines(self) -> list[EngineRecord]:
        result = await self.db.execute(select(EngineRecord).order_by(EngineRecord.id))
        return list(result.scalars().all())

    async def get_engine(self, engine_id: int) -> EngineRecord | None:
        return await self.db.get(EngineRecord, engine_id)

    async def create_engine(
        self,
        name: str,
        host: str,
        port: int,
        kind: str = "remote",
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
        )
        self.db.add(engine)
        await self.db.commit()
        await self.db.refresh(engine)
        return engine

    async def update_engine_status(
        self, engine_id: int, status: str, last_error: str | None = None
    ) -> None:
        engine = await self.db.get(EngineRecord, engine_id)
        if engine is None:
            return
        engine.status = status
        engine.last_error = last_error
        engine.last_seen = _utcnow()
        engine.updated_at = _utcnow()
        await self.db.commit()

    async def delete_engine(self, engine_id: int) -> bool:
        """Remove a machine and every device row that belongs to it.

        Returns ``True`` when a machine was removed.
        """
        engine = await self.db.get(EngineRecord, engine_id)
        if engine is None:
            return False

        result = await self.db.execute(
            select(DeviceRecord).where(DeviceRecord.engine_id == engine_id)
        )
        for device in result.scalars().all():
            await self.db.delete(device)

        await self.db.delete(engine)
        await self.db.commit()
        return True

    # ------------------------------------------------------------------ devices

    async def get_device(self, uid: str) -> DeviceRecord | None:
        return await self.db.get(DeviceRecord, uid)

    async def list_devices(self) -> list[DeviceRecord]:
        result = await self.db.execute(
            select(DeviceRecord).order_by(DeviceRecord.updated_at.desc())
        )
        return list(result.scalars().all())

    async def list_devices_page(
        self,
        *,
        keyword: str | None,
        page: int,
        page_size: int,
        sort_by: str | None = None,
        sort_order: str = "desc",
    ) -> tuple[list[DeviceRecord], int]:
        """Return one page of devices plus the total row count.

        The keyword filter and the ordering run in SQL so they cover the
        whole registry, not only the rows already loaded into the browser.
        """
        conditions = []
        needle = (keyword or "").strip().lower()
        if needle:
            pattern = f"%{needle}%"
            conditions = [
                func.lower(DeviceRecord.alias).like(pattern),
                func.lower(DeviceRecord.model).like(pattern),
                func.lower(DeviceRecord.serial).like(pattern),
                func.lower(DeviceRecord.uid).like(pattern),
                func.lower(DeviceRecord.brand).like(pattern),
            ]

        count_stmt = select(func.count()).select_from(DeviceRecord)
        page_stmt = select(DeviceRecord)
        if conditions:
            count_stmt = count_stmt.where(or_(*conditions))
            page_stmt = page_stmt.where(or_(*conditions))

        total = int((await self.db.execute(count_stmt)).scalar_one())

        sort_columns = {
            "device": func.lower(
                func.coalesce(
                    DeviceRecord.alias,
                    DeviceRecord.model,
                    DeviceRecord.serial,
                )
            ),
            "uid": DeviceRecord.uid,
            "status": DeviceRecord.status,
            "last_seen": DeviceRecord.last_seen,
        }
        sort_column = sort_columns.get(sort_by or "")
        if sort_column is not None:
            ordering = sort_column.asc() if sort_order == "asc" else sort_column.desc()
            page_stmt = page_stmt.order_by(ordering.nulls_last())
        page_stmt = page_stmt.order_by(DeviceRecord.updated_at.desc())
        page_stmt = page_stmt.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(page_stmt)
        return list(result.scalars().all()), total

    async def upsert_device(
        self,
        transport: Transport,
        entry: DeviceEntry,
        engine_id: int | None,
    ) -> DeviceRecord:
        """Insert or refresh the registry row of one device."""
        uid = build_device_uid(
            transport.kind, transport.endpoint, entry.serial, entry.hardware_id
        )
        record = await self.db.get(DeviceRecord, uid)
        now = _utcnow()

        if record is None:
            record = DeviceRecord(
                uid=uid,
                serial=entry.serial,
                engine_id=engine_id,
                transport_kind=transport.kind,
                platform="android",
                created_at=now,
            )
            self.db.add(record)

        record.serial = entry.serial
        record.engine_id = engine_id
        record.transport_kind = transport.kind
        record.model = entry.model or record.model
        record.brand = entry.brand or record.brand
        record.product = entry.product or record.product
        record.device = entry.device or record.device
        record.is_network_device = entry.is_network_device
        record.status = self._state_for(entry)
        record.last_error = None if record.status == DeviceState.ONLINE.value else record.last_error
        record.last_seen = now
        record.updated_at = now

        await self.db.commit()
        await self.db.refresh(record)
        return record

    @staticmethod
    def _state_for(entry: DeviceEntry) -> str:
        state = (entry.state or "").lower()
        if state == "device":
            return DeviceState.ONLINE.value
        if state == "unauthorized":
            return DeviceState.UNAUTHORIZED.value
        if state in ("offline", "bootloader", "recovery", "sideload"):
            return DeviceState.OFFLINE.value
        return DeviceState.ERROR.value

    async def mark_status(
        self,
        record: DeviceRecord,
        status: str,
        error: str | None = None,
    ) -> DeviceRecord:
        """Apply a lifecycle transition and persist it."""
        if record.status != status:
            logger.info(
                f"Device {record.uid} ({record.serial}) {record.status} -> {status}"
            )
        record.status = status
        record.last_error = error
        record.updated_at = _utcnow()
        if status == DeviceState.ONLINE.value:
            record.last_seen = _utcnow()
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def mark_offline_missing(self, uid: str) -> None:
        """A device that vanished from ``adb devices`` becomes offline."""
        record = await self.db.get(DeviceRecord, uid)
        if record is None:
            return
        if record.status == DeviceState.OFFLINE.value:
            return
        await self.mark_status(record, DeviceState.OFFLINE.value, "设备已断开连接")

    async def resolve(self, uid: str, transport_of: "TransportResolver") -> DeviceContext:
        """Look up a device and bind it to its transport.

        ``transport_of`` is a callable ``(engine_id) -> Transport`` supplied by
        the application wiring, so the registry stays free of transport
        construction details.
        """
        record = await self.get_device(uid)
        if record is None:
            raise KeyError(f"设备不存在: {uid}")
        transport = transport_of(record.engine_id)
        return DeviceContext(uid=uid, record=record, transport=transport)

    async def sync_transport(
        self, transport: Transport, engine_id: int | None
    ) -> list[DeviceRecord]:
        """Refresh the registry from one machine and return the known devices."""
        try:
            entries = await transport.list_devices()
        except TransportError as exc:
            logger.warning(f"list_devices failed on {transport.endpoint}: {exc}")
            if engine_id is not None:
                await self.update_engine_status(engine_id, "error", str(exc))
            return await self.list_devices()

        seen_uids: set[str] = set()
        for entry in entries:
            record = await self.upsert_device(transport, entry, engine_id)
            seen_uids.add(record.uid)

        # Remember what THIS machine reported, independent of where the device
        # rows end up being attributed.
        if engine_id is not None:
            engine = await self.db.get(EngineRecord, engine_id)
            if engine is not None and engine.device_count != len(entries):
                engine.device_count = len(entries)
                engine.updated_at = datetime.now(timezone.utc)

        # Devices of this machine that disappeared are offline, not deleted.
        for record in await self.list_devices():
            if record.engine_id != engine_id or record.transport_kind != transport.kind:
                continue
            if record.uid in seen_uids:
                continue
            await self.mark_offline_missing(record.uid)

        if engine_id is not None:
            await self.update_engine_status(engine_id, "connected")
        return await self.list_devices()
