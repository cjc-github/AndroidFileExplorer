"""Platform settings persisted in the database (a simple key/value table)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SettingRecord(SQLModel, table=True):
    """One platform setting, stored as a string value."""

    __tablename__ = "settings"

    key: str = Field(primary_key=True, max_length=100)
    value: str = Field(default="", max_length=200)
    updated_at: datetime = Field(default_factory=_utcnow)
