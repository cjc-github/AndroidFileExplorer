"""Persistence models: remote engines and the device registry."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EngineRecord(SQLModel, table=True):
    """A machine that hosts an ADB server (the local host or a remote engine)."""

    __tablename__ = "engines"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default="", max_length=100)
    host: str = Field(default="127.0.0.1", max_length=200, index=True)
    port: int = Field(default=5037)
    kind: str = Field(default="local", max_length=20)
    """``local`` for this host, ``remote`` for an MDD engine."""
    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""

    ssh_username: str | None = Field(default=None, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=200)
    """SSH credentials for kind="ssh" machines (never echoed by the API)."""
    ssh_port: int = Field(default=22)
    adb_path: str | None = Field(default=None, max_length=300)
    """adb binary on the remote machine when it is not in PATH."""
    device_count: int = Field(default=0)
    """Devices seen by the LAST probe/sync of this machine.

    Device identity is engine-independent (uid = hw serial), so counting the
    device rows per engine is zero-sum when several engines see the same
    phone; this column keeps what the engine ACTUALLY reported last time.
    """
    status: str = Field(default="unknown", max_length=20, index=True)
    """unknown / connected / disconnected / error."""
    last_error: str | None = Field(default=None, max_length=1000)
    last_seen: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class DeviceRecord(SQLModel, table=True):
    """One Android device, bound to a machine by its stable unique id."""

    __tablename__ = "devices"

    uid: str = Field(primary_key=True, max_length=64)
    """Stable unique identifier: sha1(kind:endpoint:serial)[:16]."""

    serial: str = Field(default="", max_length=200, index=True)
    engine_id: int | None = Field(default=None, foreign_key="engines.id", index=True)
    transport_kind: str = Field(default="local", max_length=20)

    alias: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=100)
    cpu_abi: str | None = Field(default=None, max_length=100)
    cpu_cores: int | None = Field(default=None)
    """Core count from /proc/cpuinfo."""
    cpu_max_freq_khz: int | None = Field(default=None)
    """Highest per-core maximum frequency in kHz (cpufreq sysfs)."""
    board_platform: str | None = Field(default=None, max_length=100)
    """SoC / board platform, from ro.board.platform."""
    """The CPU architecture, from ro.product.cpu.abi (e.g. arm64-v8a)."""
    android_version: str | None = Field(default=None, max_length=40)
    """The Android version string, from ro.build.version.release."""
    api_level: str | None = Field(default=None, max_length=10)
    """The API level, from ro.build.version.sdk."""
    product: str | None = Field(default=None, max_length=200)
    device: str | None = Field(default=None, max_length=200)
    platform: str = Field(default="android", max_length=20)

    status: str = Field(default="offline", max_length=30, index=True)
    """online / offline / unauthorized / permission_denied / error."""

    is_rooted: bool = Field(default=False)
    is_network_device: bool = Field(default=False)
    """True for ip:port devices (the only ones `adb connect` accepts)."""
    last_error: str | None = Field(default=None, max_length=1000)
    last_seen: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
