"""Read / write the platform settings (a KV table with typed defaults)."""

from __future__ import annotations

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.settings import SettingRecord
from app.schemas.settings import (
    DEFAULT_AUTO_ROOT,
    DEFAULT_AUTO_SYNC,
    DEFAULT_SYNC_INTERVAL,
    SettingsInfo,
    SettingsUpdate,
)

_KEYS = ("sync_interval_seconds", "auto_root", "auto_sync_seconds")


class SettingsService:
    """Typed access to the platform settings."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def read(self) -> SettingsInfo:
        rows = (await self.db.execute(select(SettingRecord))).scalars().all()
        values = {row.key: row.value for row in rows}

        def _int(key: str, default: int) -> int:
            raw = values.get(key)
            try:
                return int(raw) if raw is not None and raw != "" else default
            except ValueError:
                return default

        def _bool(key: str, default: bool) -> bool:
            raw = values.get(key)
            if raw is None or raw == "":
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        return SettingsInfo(
            sync_interval_seconds=_int("sync_interval_seconds", DEFAULT_SYNC_INTERVAL),
            auto_root=_bool("auto_root", DEFAULT_AUTO_ROOT),
            auto_sync_seconds=_int("auto_sync_seconds", DEFAULT_AUTO_SYNC),
        )

    async def update(self, payload: SettingsUpdate) -> SettingsInfo:
        """Replace every setting (the frontend always sends the full form)."""
        values: dict[str, str] = {
            "sync_interval_seconds": str(payload.sync_interval_seconds),
            "auto_root": "true" if payload.auto_root else "false",
            "auto_sync_seconds": str(payload.auto_sync_seconds),
        }
        for key, value in values.items():
            record = await self.db.get(SettingRecord, key)
            if record is None:
                self.db.add(SettingRecord(key=key, value=value))
            elif record.value != value:
                record.value = value
        await self.db.commit()
        return await self.read()
