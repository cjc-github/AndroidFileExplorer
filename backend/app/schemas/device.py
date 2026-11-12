"""Pydantic schemas for the device / file / engine APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class EngineCreate(BaseModel):
    """Payload to register a remote machine."""

    name: str = Field(default="", max_length=100)
    host: str = Field(min_length=1, max_length=200)
    port: int = Field(default=12345, ge=1, le=65535)
    kind: str = Field(default="remote", pattern="^(local|remote|ssh)$")
    token: str | None = Field(default=None, max_length=200)
    """Shared secret for the device agent (None = no authentication)."""
    ssh_username: str | None = Field(default=None, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=200)
    """SSH credentials for kind="ssh" machines."""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    adb_path: str | None = Field(default=None, max_length=300)

    @field_validator("host")
    @classmethod
    def strip_host(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("host 不能为空")
        return cleaned


class EngineUpdate(BaseModel):
    """Payload to edit a registered machine.

    Secret fields follow the "blank means keep" rule: the edit form never
    receives the stored token/password, so an empty value must not wipe it.
    ``adb_path`` may be cleared explicitly with an empty string.
    """

    name: str | None = Field(default=None, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    token: str | None = Field(default=None, max_length=200)
    ssh_username: str | None = Field(default=None, max_length=100)
    ssh_password: str | None = Field(default=None, max_length=200)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    adb_path: str | None = Field(default=None, max_length=300)

    @field_validator("host")
    @classmethod
    def strip_optional_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("host 不能为空")
        return cleaned


class EngineInfo(BaseModel):
    """View model of a machine that hosts ADB."""

    id: int
    ssh_username: str | None = None
    ssh_port: int = 22
    has_ssh_password: bool = False
    """True when an SSH password is stored; the value itself never leaves."""
    adb_path: str | None = None
    name: str
    host: str
    port: int
    kind: str
    status: str
    last_error: str | None = None
    last_seen: datetime | None = None
    created_at: datetime
    updated_at: datetime
    device_count: int = 0
    """How many devices are registered on this machine."""

    model_config = {"from_attributes": True}


class EngineTestResult(BaseModel):
    """Result of probing one machine."""

    engine_id: int
    status: str
    device_count: int = 0


class StorageInfo(BaseModel):
    """Usage of one mount point (bytes)."""

    path: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    available_bytes: int | None = None


class DeviceMetrics(BaseModel):
    """CPU / memory / storage snapshot of one device."""

    cpu_percent: float | None = None
    cpu_user_percent: float | None = None
    cpu_system_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    memory_used_bytes: int | None = None
    memory_percent: float | None = None
    cpu_current_freq_khz: int | None = None
    storage: list[StorageInfo] | None = None
    load_average: list[float] | None = None
    sampled_at: datetime


class DeviceInfo(BaseModel):
    """View model of a device with its lifecycle state."""

    uid: str
    serial: str
    engine_id: int | None = None
    transport_kind: str = "local"
    alias: str | None = None
    model: str | None = None
    brand: str | None = None
    cpu_abi: str | None = None
    cpu_cores: int | None = None
    cpu_max_freq_khz: int | None = None
    board_platform: str | None = None
    android_version: str | None = None
    api_level: str | None = None
    product: str | None = None
    device: str | None = None
    platform: str = "android"
    status: str
    is_rooted: bool = False
    is_network_device: bool = False
    """True for ``ip:port`` devices, the only ones `adb connect` accepts."""
    last_error: str | None = None
    last_seen: datetime | None = None
    created_at: datetime
    updated_at: datetime
    metrics: DeviceMetrics | None = None

    model_config = {"from_attributes": True}


class DevicePage(BaseModel):
    """One page of the device list."""

    items: list[DeviceInfo] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class FileInfo(BaseModel):
    """One entry of a device directory listing."""

    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified_at: datetime | None = None
    permissions: str | None = None
    owner: str | None = None
    group: str | None = None
    is_link: bool = False
    link_target: str | None = None


class PathPart(BaseModel):
    """Breadcrumb segment."""

    name: str
    path: str


class FileListResponse(BaseModel):
    """Result of a directory listing / search."""

    current_path: str
    parent_path: str | None = None
    parts: list[PathPart] = Field(default_factory=list)
    items: list[FileInfo] = Field(default_factory=list)
    truncated: bool = False


class PullRequest(BaseModel):
    """Pull a device file into the server side download directory."""

    path: str = Field(min_length=1)
    """Device side absolute path."""


class PushRequest(BaseModel):
    """Push a local file onto a device."""

    path: str = Field(min_length=1)
    """Device side destination path."""
    local_path: str = Field(min_length=1)
    """Absolute path of the file on the API server."""


class MkdirRequest(BaseModel):
    """Create a directory on the device."""

    path: str = Field(min_length=1, description="设备上的新目录路径")


class RenameRequest(BaseModel):
    """Rename or move a file / directory on the device."""

    source: str = Field(min_length=1, description="原路径")
    target: str = Field(min_length=1, description="新路径")


class ArchiveSelectionRequest(BaseModel):
    """Selected device paths to zip into one archive."""

    paths: list[str] = Field(min_length=1, max_length=200, description="选中的路径列表")


class CopyRequest(BaseModel):
    """Copy a file / directory on the device."""

    source: str = Field(min_length=1, description="源路径")
    target: str = Field(min_length=1, description="目标路径")


class PermissionsRequest(BaseModel):
    """Change the permissions / owner of one device path."""

    path: str = Field(min_length=1, description="要修改的路径")
    mode: str | None = Field(default=None, max_length=8, description="八进制权限，例如 644")
    owner: str | None = Field(default=None, max_length=100, description="所有者")
    group: str | None = Field(default=None, max_length=100, description="用户组")


class RemoveRequest(BaseModel):
    """Delete a file / directory on the device."""

    path: str = Field(min_length=1, description="要删除的路径")
    recursive: bool = Field(default=False, description="递归删除目录")


class BatchRootRequest(BaseModel):
    """Apply a root switch to several devices at once."""

    uids: list[str] = Field(min_length=1, description="要操作的设备 UID 列表")
    enable: bool = Field(default=True, description="True=尝试 root，False=取消 root")


class BatchResultItem(BaseModel):
    """The outcome for a single device of a batch operation."""

    uid: str
    ok: bool
    message: str = ""
    is_rooted: bool | None = None


class DeviceAliasRequest(BaseModel):
    """Set the human readable name of a device."""

    alias: str = Field(default="", max_length=200)


class TransferInfo(BaseModel):
    """Outcome of a pull/push operation."""

    ok: bool
    path: str
    size: int | None = None
    stored_at: str | None = None
    message: str = ""
