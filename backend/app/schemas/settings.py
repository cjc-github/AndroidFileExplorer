"""Platform settings: schema and defaults."""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Frontend device table refresh interval (seconds).
DEFAULT_SYNC_INTERVAL = 3
#: Backend auto device discovery loop (seconds; 0 = manual sync only).
DEFAULT_AUTO_SYNC = 0
#: Try `adb root` for every online non-rooted device after a sync.
DEFAULT_AUTO_ROOT = False

SYNC_INTERVAL_RANGE = (1, 60)
AUTO_SYNC_RANGE = (0, 86400)


class SettingsInfo(BaseModel):
    """The effective platform settings."""

    sync_interval_seconds: int = DEFAULT_SYNC_INTERVAL
    auto_root: bool = DEFAULT_AUTO_ROOT
    auto_sync_seconds: int = DEFAULT_AUTO_SYNC


class SettingsUpdate(BaseModel):
    """Payload to replace the platform settings."""

    sync_interval_seconds: int = Field(
        ge=SYNC_INTERVAL_RANGE[0], le=SYNC_INTERVAL_RANGE[1]
    )
    auto_root: bool
    auto_sync_seconds: int = Field(ge=AUTO_SYNC_RANGE[0], le=AUTO_SYNC_RANGE[1])
