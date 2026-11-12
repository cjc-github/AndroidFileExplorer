"""Transport abstraction shared by local (adb) and remote (MDD) machine backends.

The rest of the service only talks to this interface, so a device discovered on
the local host and a device discovered on a remote engine behave identically.
"""

from __future__ import annotations

import posixpath
import asyncio
import shlex
import time
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .agent_protocol import unwrap_agent_output


class TransportError(RuntimeError):
    """Base class for every transport level failure."""


class PermissionDeniedError(TransportError):
    """The device refused the operation (root/su required, or path protected)."""


class DeviceNotFoundError(TransportError):
    """The requested device is unknown to the transport."""


class DeviceUnauthorizedError(TransportError):
    """The device is visible but not authorized for debugging (unauthorized state)."""


class DeviceOfflineError(TransportError):
    """The device is known but not reachable right now."""


class TransportTimeoutError(TransportError):
    """The remote host did not answer within the configured budget."""


class DeviceState(str, Enum):
    """Normalized device lifecycle states."""

    ONLINE = "online"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"


#: Marker used by the fake/real engines to separate procfs payloads.
PROCFS_MARKER = "---AFE---"
#: Second marker: the device property section follows the procfs payloads.
PROPS_MARKER = "---AFEPROPS---"

#: The single shell round trip that collects the procfs metrics.
#:
#: `getprop` is deliberately NOT part of it: on a real device `getprop` only
#: resolves when it is the ONLY command of the shell invocation
#: (``adb shell getprop x`` works, ``adb shell "cat a; getprop x"`` does not).
#: The brand is therefore fetched by its own command.
METRICS_COMMAND = (
    "cat /proc/stat; "
    f"echo '{PROCFS_MARKER}'; "
    "cat /proc/meminfo; "
    f"echo '{PROPS_MARKER}'; "
    "cat /proc/loadavg"
)

#: Separate command for the device brand (must be the only command).
BRAND_COMMAND = "getprop ro.product.brand"

#: The device properties shown in the device table.  One `getprop` dump
#: answers all of them; the values never change while the device boots.
PROPERTY_KEYS = (
    "ro.product.cpu.abi",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.board.platform",
)

_PERMISSION_MARKERS = (
    "permission denied",
    "operation not permitted",
    "requires root",
    "root access",
    "su: not found",
    "permission denied (su)",
    "inaccessible or not found",
    "access denied",
)

_UNAUTHORIZED_MARKERS = (
    "device unauthorized",
    "unauthorized",
)

# Only unambiguous offline signals.  Broad substrings such as "not found"
# or "closed" also matched harmless messages (a shell answering
# "sh: missing-tool: not found", an unrelated log line) and misclassified
# a healthy device as offline.
_OFFLINE_MARKERS = (
    "device offline",
    "no devices/emulators found",
    "connection reset",
    "connection closed",
    "broken pipe",
    "cannot connect to daemon",
)

#: adb reports a vanished device as ``error: device 'emulator-5554' not
#: found`` - the serial sits between the two words, so a plain "device not
#: found" marker never matches it.  The pattern below catches the device
#: form while leaving a shell's "sh: tool: not found" alone.
_OFFLINE_DEVICE_GONE = re.compile(r"device\s+'[^']*'\s+not found")

#: How long one ``id`` probe result is trusted.  The root flag only changes
#: when adbd restarts (``adb root`` / ``adb unroot``), while the foreground
#: polls every few seconds - without the cache every poll would pay one
#: shell round trip per device just to ask for the identity.
ROOT_CACHE_TTL_SECONDS = 15.0


@dataclass(slots=True)
class DeviceEntry:
    """One Android device seen by a transport."""

    serial: str
    """adb serial (usb id) or ip:port for network devices."""

    state: str = "device"
    """Raw adb state: device / offline / unauthorized / bootloader."""

    model: str | None = None
    brand: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None

    hardware_id: str | None = None
    """The device's own serial (``ro.serialno``).

    The unique id of a device must NOT depend on the machine it was reached
    through: the same phone behind two different agents is still the same
    phone and must not show up twice.
    """

    @property
    def is_online(self) -> bool:
        return self.state == "device"

    @property
    def is_network_device(self) -> bool:
        return ":" in self.serial and not self.serial.startswith("emulator-")


@dataclass(slots=True)
class FileEntry:
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


@dataclass(slots=True)
class MetricsSample:
    """One CPU / memory sample of a device."""

    cpu_percent: float | None = None
    cpu_user_percent: float | None = None
    cpu_system_percent: float | None = None
    memory_total_bytes: int | None = None
    memory_available_bytes: int | None = None
    memory_used_bytes: int | None = None
    memory_percent: float | None = None
    load_average: tuple[float, float, float] | None = None
    uptime_seconds: float | None = None
    sampled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    raw_total_jiffies: int = 0
    """Raw cumulative CPU counter of /proc/stat (for delta computation)."""

    raw_idle_jiffies: int = 0
    """Raw cumulative idle counter of /proc/stat (for delta computation)."""

    raw_user_jiffies: int = 0
    """Raw cumulative user counter of /proc/stat."""

    raw_system_jiffies: int = 0
    """Raw cumulative system counter of /proc/stat."""

    brand: str | None = None
    """ro.product.brand of the device, when the device reports one."""


@dataclass(slots=True)
class TransferResult:
    """Outcome of a pull/push operation."""

    ok: bool
    path: str
    size: int | None = None
    stored_at: str | None = None
    message: str = ""


def classify_output(output: str) -> TransportError | None:
    """Map a raw command output onto a typed transport error.

    Returns ``None`` when the output looks successful.
    """
    lowered = output.lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        return PermissionDeniedError(output.strip() or "permission denied")
    if any(marker in lowered for marker in _UNAUTHORIZED_MARKERS):
        return DeviceUnauthorizedError(output.strip() or "device unauthorized")
    if any(marker in lowered for marker in _OFFLINE_MARKERS):
        return DeviceOfflineError(output.strip() or "device offline")
    if _OFFLINE_DEVICE_GONE.search(lowered):
        return DeviceOfflineError(output.strip() or "device not found")
    return None


#: Characters that must never appear in a device path.  They are shell
#: metacharacters, and `adb shell` re-parses the command on the device where
#: the quoting of the agent side is already gone.
FORBIDDEN_PATH_CHARS = set(";|&$`<>()\"'\n\r\t*?[]{}!#~%^,")


class UnsafePathError(ValueError):
    """A path contains characters that could break out of the command."""


def assert_safe_device_path(path: str) -> str:
    """Reject a path that could inject a shell command.

    ``adb shell`` runs the command again on the device, so quoting on the
    agent side is not enough: a path like ``/sdcard/x$(id)`` would run ``id``
    on the device.  Only harmless characters are allowed.
    """
    normalized = normalize_device_path(path)
    if normalized == "/":
        return normalized
    for segment in normalized.split("/"):
        if not segment:
            continue
        bad = FORBIDDEN_PATH_CHARS & set(segment)
        if bad:
            raise UnsafePathError(
                f"路径包含非法字符 {''.join(sorted(bad))!r}：{normalized}"
            )
    return normalized


def normalize_device_path(path: str) -> str:
    """Normalize a device side path to an absolute POSIX path without trailing slash."""
    if not path or path == "/":
        return "/"
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    normalized = posixpath.normpath(cleaned)
    return normalized if normalized.startswith("/") else "/" + normalized


def directory_listing_path(path: str) -> str:
    """Return the path to hand to ``ls -l`` so that a directory is listed.

    Android's toybox ``ls`` describes a symlink argument instead of listing
    its content, so ``/sdcard`` would show only the link. A trailing slash
    forces the directory listing.
    """
    normalized = normalize_device_path(path)
    return normalized if normalized.endswith("/") else normalized + "/"


def join_device_path(base: str, name: str) -> str:
    """Join a device path and an entry name."""
    if base == "/":
        return "/" + name
    return posixpath.join(base, name)


def parent_device_path(path: str) -> str:
    """Return the parent directory of a device path ('' means filesystem root)."""
    normalized = normalize_device_path(path)
    if normalized == "/":
        return ""
    parent = posixpath.dirname(normalized)
    return parent if parent != "/" else "/"


def path_parts(path: str) -> list[tuple[str, str]]:
    """Split a device path into breadcrumb parts.

    >>> path_parts("/sdcard/Android")
    [('sdcard', '/sdcard'), ('Android', '/sdcard/Android')]
    """
    normalized = normalize_device_path(path)
    if normalized == "/":
        return []
    parts: list[tuple[str, str]] = []
    current = ""
    for segment in normalized.strip("/").split("/"):
        if not segment:
            continue
        current = f"{current}/{segment}"
        parts.append((segment, current))
    return parts


_SIZE_RE = re.compile(r"^\s*(\d+)\s*$")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_FIELDS = frozenset(
    {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
)


def _parse_ls_datetime(fields: list[str]) -> datetime | None:
    """Parse the date fields of one ``ls -l`` row.

    Three dialects exist in the wild:

    * toybox (Android): ``2024-01-01 12:00`` - two fields.
    * toybox for files older than ~6 months: ``2024-01-01`` - one field, the
      time is omitted.
    * busybox: ``Jan  1 12:00`` / ``Jan  1  2024`` - three fields after
      ``split()`` collapsed the padding; a missing year is completed with
      the current one.

    Returns ``None`` for layouts it does not recognize: the modification
    time is optional in the UI, so an unparsable date must not break the
    listing.
    """
    if len(fields) >= 2 and _ISO_DATE_RE.match(fields[0]):
        try:
            return datetime.strptime(f"{fields[0]} {fields[1]}", "%Y-%m-%d %H:%M")
        except ValueError:
            # A time with seconds ("12:00:00") is not the toybox default,
            # but it is still a date we can show.
            try:
                return datetime.strptime(
                    f"{fields[0]} {fields[1]}", "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                return None
    if len(fields) >= 1 and _ISO_DATE_RE.match(fields[0]):
        # Date only (toybox drops the time for old files).
        try:
            return datetime.strptime(fields[0], "%Y-%m-%d")
        except ValueError:
            return None
    if len(fields) >= 3 and fields[0] in _MONTH_FIELDS:
        for pattern, has_year in (("%b %d %H:%M", False), ("%b %d %Y", True)):
            try:
                parsed = datetime.strptime(
                    f"{fields[0]} {fields[1]} {fields[2]}", pattern
                )
            except ValueError:
                continue
            return parsed if has_year else parsed.replace(year=datetime.now().year)
    return None


def _is_root_identity(output: str) -> bool:
    """True when an ``id`` output shows uid 0 (root)."""
    text = (output or "").strip()
    if not text:
        return False
    if text.startswith("uid=0("):
        return True
    return bool(text) and "uid=0(root)" in text


def parse_long_listing(output: str, base_path: str) -> list[FileEntry]:
    """Parse ``ls -lAN`` output of a device into :class:`FileEntry` items.

    Handles the toybox/busybox ``ls -l`` format::

        -rw-rw-rw- 1 root root 4096 2024-01-01 12:00 name
        drwxrwx--- 2 root root 4096 2024-01-01 12:00 subdir
        lrwxrwxrwx 1 root root   11 2024-01-01 12:00 link -> target
    """
    entries: list[FileEntry] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("total "):
            continue
        if line.startswith("ls:"):
            raise PermissionDeniedError(line)

        parts = line.split()
        if len(parts) < 4:
            continue
        permissions = parts[0]
        if not re.match(r"^[-dlbcps][-rwxsStTlL+]{9}", permissions):
            # Not a long listing row (e.g. an error message) -> skip.
            continue

        is_dir = permissions[0] == "d"
        is_link = permissions[0] == "l"

        # Layout: perms links owner group size date time name...
        # toybox puts an ISO date at 5 and the time at 6; busybox puts the
        # month at 5, the day at 6 and the time (or the year) at 7, which
        # shifts the name column.  toybox omits the time for old files, so
        # the name column shifts back to 6 there.  Parse what is
        # recognizable, never guess.
        modified_at: datetime | None = None
        name_column = 7
        if len(parts) >= 7 and _ISO_DATE_RE.match(parts[5]):
            # The time is present when there are at least 8 columns.
            if len(parts) >= 8 and not _ISO_DATE_RE.match(parts[6]):
                modified_at = _parse_ls_datetime(parts[5:7])
            else:
                modified_at = _parse_ls_datetime(parts[5:6])
                name_column = 6
        elif len(parts) >= 9 and parts[5] in _MONTH_FIELDS:
            modified_at = _parse_ls_datetime(parts[5:8])
            name_column = 8

        # The name column starts after the fixed fields (perms, links, owner,
        # group, size, date, time); everything after it belongs to the name.
        size: int | None = None
        try:
            size = int(parts[4])
        except (ValueError, IndexError):
            size = None

        owner = parts[2] if len(parts) > 2 else None
        group = parts[3] if len(parts) > 3 else None

        if len(parts) <= name_column:
            continue
        name_parts = parts[name_column:]
        if not name_parts:
            continue
        name = " ".join(name_parts)
        link_target: str | None = None
        if is_link and " -> " in name:
            name, link_target = name.split(" -> ", 1)

        if name in (".", ".."):
            continue

        entries.append(
            FileEntry(
                name=name,
                path=join_device_path(base_path, name),
                is_dir=is_dir,
                size=size,
                modified_at=modified_at,
                permissions=permissions,
                owner=owner,
                group=group,
                is_link=is_link,
                link_target=link_target,
            )
        )
    return entries


class Transport(ABC):
    """A machine (local host or remote engine) that can reach Android devices."""

    kind: str = "unknown"

    def _brand_cache(self) -> dict[str, str]:
        """Cache of ro.product.brand per device serial (it never changes).

        Created lazily: the concrete transports do not all call
        ``super().__init__()``, so the cache cannot live in the constructor.
        """
        cache = getattr(self, "_brands_cache", None)
        if cache is None:
            cache = {}
            self._brands_cache = cache
        return cache

    @property
    @abstractmethod
    def endpoint(self) -> str:
        """Stable identity of the machine, e.g. ``local`` or ``host:port``."""

    @abstractmethod
    async def list_devices(self) -> list[DeviceEntry]:
        """Enumerate the Android devices reachable from this machine."""

    @abstractmethod
    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        """Run a shell command on a device and return its stdout+stderr text."""

    @abstractmethod
    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        """List the entries of a device directory."""

    @abstractmethod
    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        """Copy a device file to a local path."""

    @abstractmethod
    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        """Copy a local file onto the device."""

    async def is_rooted(self, serial: str) -> bool:
        """True when the device shell runs as uid 0 (root).

        The default implementation asks the device for its identity, so every
        transport supports it without extra code.  The answer is cached for a
        short window (the flag only changes when adbd restarts, and the
        foreground polls every few seconds), and :meth:`set_root` drops the
        entry so the next probe re-samples after a root switch.  A failed
        probe is NOT cached - it is usually transient.
        """
        cache = self._root_cache()
        cached = cache.get(serial)
        now = time.monotonic()
        if cached is not None and (now - cached[1]) < ROOT_CACHE_TTL_SECONDS:
            return cached[0]
        try:
            output = await self.shell(serial, "id", timeout=5)
        except TransportError:
            return False
        rooted = _is_root_identity(output)
        cache[serial] = (rooted, now)
        return rooted

    def _root_cache(self) -> dict[str, tuple[bool, float]]:
        """Cache of the ``id`` probe result per device serial.

        Created lazily: not every transport calls ``super().__init__()``,
        so the cache cannot live in the constructor.
        """
        cache = getattr(self, "_rooted_cache", None)
        if cache is None:
            cache = {}
            self._rooted_cache = cache
        return cache

    async def set_root(self, serial: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable the root shell of a device.

        ``adb root`` / ``adb unroot`` restart adbd, so the device goes offline
        for a few seconds afterwards.  This method therefore:

        1. waits for the device to be online BEFORE issuing the switch (a
           double click or a quick root->unroot toggle would otherwise land
           inside the restart window of the previous call);
        2. retries ONCE after the device comes back when the failure looks
           transient (offline / timeout / device not found);
        3. only reports failure when the retry also fails, with the real
           reason in the message.

        The output of the agent_server is unwrapped before it is evaluated,
        and the return code of ``adb root`` is ignored on purpose (it always
        exits non zero because adbd restarts) - the output text decides.
        """
        verb = "切换 root 模式" if enabled else "取消 root"

        # Wait out any restart window left by a PREVIOUS switch.
        await self._wait_until_online(serial, timeout=15.0)

        transient_markers = (
            "offline",
            "timeout",
            "waiting for device",
            "device not found",
            "no devices",
            "closed",
        )

        last_message = ""
        for attempt in range(2):
            try:
                raw = (
                    await self.adb_root(serial)
                    if enabled
                    else await self.adb_unroot(serial)
                )
            except TransportError as exc:
                last_message = str(exc)
                lowered_exc = last_message.lower()
                if attempt == 0 and any(m in lowered_exc for m in transient_markers):
                    # Landed inside a restart window: wait, then try once more.
                    await self._wait_until_online(serial, timeout=30.0)
                    continue
                return False, f"{verb}失败: {last_message}"

            # The flag may have changed: drop the cached identity probe so the
            # next ``is_rooted`` call re-samples instead of reporting the old
            # value for the rest of the cache window.
            self._root_cache().pop(serial, None)

            output = unwrap_agent_output(raw or "")[1]
            lowered = (output or "").lower()

            if "restarting adbd" in lowered:
                await self._wait_until_online(serial, timeout=30.0)

            if "cannot run as root" in lowered:
                return False, (
                    f"该设备不允许以 root 运行（生产版本设备）：{output.strip()}"
                    if output.strip()
                    else "该设备不允许以 root 运行（生产版本）"
                )

            if enabled:
                if (
                    "restarting adbd as root" in lowered
                    or "already running as root" in lowered
                ):
                    return True, output.strip() or "已以 root 模式重启 adbd"
            else:
                if (
                    "restarting adbd as non root" in lowered
                    or "already running as non root" in lowered
                ):
                    return True, output.strip() or "已恢复为普通用户模式"
                # The device was not rooted in the first place: the request is
                # already satisfied, so this is a success and not an error.
                if "not running as root" in lowered:
                    return True, "设备本来就未以 root 运行"

            # Unrecognized output: if it looks like a transient device error,
            # wait for the device and retry once before giving up.
            if attempt == 0 and any(m in lowered for m in transient_markers):
                await self._wait_until_online(serial, timeout=30.0)
                continue
            return False, output.strip() or f"{verb}失败"

        return False, f"{verb}失败: {last_message}" if last_message else f"{verb}失败"

    async def _wait_until_online(self, serial: str, timeout: float = 30.0) -> bool:
        """Wait until the device shell answers again.

        Used after ``adb root`` / ``adb unroot``: adbd restarts and the device
        disappears for a few seconds.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                await self.shell(serial, "echo online", timeout=5)
                return True
            except TransportError:
                await asyncio.sleep(1.0)
        return False

    async def try_root(self, serial: str) -> tuple[bool, str]:
        """Try to restart adbd with root privileges.

        Returns ``(success, message)``. The device must be a userdebug /
        emulator build - a production build answers "adbd cannot run as root
        in production builds".
        """
        try:
            output = await self.shell(serial, "", timeout=10)
        except TransportError:
            output = ""
        return await self.set_root(serial, True)

    async def adb_unroot(self, serial: str) -> str:
        """Restart adbd without root privileges.

        Not abstract on purpose: a transport that cannot change the state
        simply says so instead of breaking every implementation.
        """
        try:
            return await self.shell(serial, "", timeout=10)
        except TransportError as exc:
            return str(exc)

    async def adb_root(self, serial: str) -> str:
        """Run ``adb root`` for one device and return its output.

        Not abstract on purpose: a transport that cannot escalate simply says
        so instead of breaking every implementation.
        """
        try:
            return await self.shell(serial, "", timeout=10)
        except TransportError as exc:
            return str(exc)

    async def mkdir(self, serial: str, path: str) -> None:
        """Create a directory (including its parents)."""
        normalized = assert_safe_device_path(path)
        if normalized == "/":
            raise ValueError("不能创建根目录")
        await self.shell(serial, f"mkdir -p {shlex.quote(normalized)}", timeout=20)

    async def rename(self, serial: str, source: str, target: str) -> None:
        """Move / rename a file or directory."""
        source_path = assert_safe_device_path(source)
        target_path = assert_safe_device_path(target)
        for value in (source_path, target_path):
            if value == "/":
                raise ValueError("不能操作根目录")
        if source_path == target_path:
            # `mv a a` fails with a cryptic "Invalid argument"; refuse it here.
            raise ValueError("新名称与原名称相同")
        await self.shell(
            serial,
            f"mv {shlex.quote(source_path)} {shlex.quote(target_path)}",
            timeout=30,
        )

    async def copy(self, serial: str, source: str, target: str) -> None:
        """Copy a file or directory (recursively)."""
        source_path = assert_safe_device_path(source)
        target_path = assert_safe_device_path(target)
        for value in (source_path, target_path):
            if value == "/":
                raise ValueError("不能操作根目录")
        if source_path == target_path:
            raise ValueError("源路径与目标路径相同")
        await self.shell(
            serial,
            f"cp -r {shlex.quote(source_path)} {shlex.quote(target_path)}",
            timeout=120,
        )

    async def change_permissions(
        self,
        serial: str,
        path: str,
        mode: str | None = None,
        owner: str | None = None,
        group: str | None = None,
    ) -> None:
        """Apply ``chmod`` / ``chown`` to one path.

        The mode and the owner/group names are validated before they reach
        the shell; the path itself is covered by the shared whitelist.
        """
        normalized = assert_safe_device_path(path)
        if normalized == "/":
            raise ValueError("不能修改根目录权限")
        mode = (mode or "").strip() or None
        owner = (owner or "").strip() or None
        group = (group or "").strip() or None
        if mode is not None and not re.fullmatch(r"[0-7]{3,4}", mode):
            raise ValueError("权限模式必须是 3~4 位八进制数字，例如 644")
        for value in (owner, group):
            if value is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise ValueError("所有者/用户组只能包含字母、数字、下划线、点和短横线")
        commands: list[str] = []
        if mode is not None:
            commands.append(f"chmod {mode} {shlex.quote(normalized)}")
        if owner is not None or group is not None:
            if owner is not None and group is not None:
                spec = f"{owner}:{group}"
            elif owner is not None:
                spec = owner
            else:
                spec = f":{group}"
            commands.append(f"chown {shlex.quote(spec)} {shlex.quote(normalized)}")
        if not commands:
            raise ValueError("至少需要提供权限模式或所有者")
        await self.shell(serial, " && ".join(commands), timeout=20)

    async def remove(self, serial: str, path: str, recursive: bool = False) -> None:
        """Delete a file (or a directory with ``recursive``)."""
        normalized = assert_safe_device_path(path)
        if normalized == "/":
            raise ValueError("不能删除根目录")
        flag = "-rf" if recursive else "-f"
        await self.shell(serial, f"rm {flag} {shlex.quote(normalized)}", timeout=30)

    @abstractmethod
    async def connect(self, serial: str) -> tuple[bool, str]:
        """Establish the ADB connection of a network device."""

    @abstractmethod
    async def disconnect(self, serial: str) -> tuple[bool, str]:
        """Drop the ADB connection of a network device."""

    async def metrics(self, serial: str) -> MetricsSample:
        """Collect one CPU / memory / load sample of a device."""
        raw = await self.shell(serial, METRICS_COMMAND)
        sample = parse_metrics(raw)
        sample.brand = await self.device_brand(serial)
        return sample

    async def device_hardware_id(self, serial: str) -> str | None:
        """The device's own serial number (``ro.serialno``).

        Used to identify a device independently of the machine it is reached
        through.  Not every device reports one (and the emulator reuses
        ``emulator-5554`` everywhere), so ``None`` is a valid answer.
        """
        for prop in ("ro.serialno", "ro.boot.serialno"):
            try:
                value = (await self.shell(serial, f"getprop {prop}", timeout=5)).strip()
            except TransportError:
                continue
            if value and "not found" not in value.lower() and "unknown" not in value.lower():
                return value
        return None

    def _props_cache(self) -> dict[str, dict[str, str]]:
        """Cache of the device properties per serial (they never change)."""
        cache = getattr(self, "_device_props_cache", None)
        if cache is None:
            cache = {}
            self._device_props_cache = cache
        return cache

    async def device_props(self, serial: str) -> dict[str, str]:
        """Read the static device properties (ABI, Android version, API).

        One `getprop` dump answers everything; on devices whose dump is not
        parseable the individual keys are asked one by one.  Cached per
        device because the values do not change until the next reboot into a
        different system image.
        """
        cache = self._props_cache()
        cached = cache.get(serial)
        if cached is not None:
            return cached

        props: dict[str, str] = {}
        try:
            dump = await self.shell(serial, "getprop", timeout=10)
        except TransportError:
            dump = ""

        import re as _re

        for match in _re.finditer(r"\[([^]]+)\]: \[([^]]*)\]", dump):
            key, value = match.group(1), match.group(2).strip()
            if key in PROPERTY_KEYS and value:
                props[key] = value

        if len(props) < len(PROPERTY_KEYS):
            # Older / exotic builds: ask the missing keys individually.
            for key in PROPERTY_KEYS:
                if key in props:
                    continue
                try:
                    value = (
                        await self.shell(serial, f"getprop {key}", timeout=5)
                    ).strip()
                except TransportError:
                    continue
                if value and "not found" not in value.lower():
                    props[key] = value

        cache[serial] = props
        return props

    def _cpu_info_cache(self) -> dict[str, tuple[int | None, int | None]]:
        """Cache of the CPU hardware facts per serial (they never change)."""
        cache = getattr(self, "_cpu_facts_cache", None)
        if cache is None:
            cache = {}
            self._cpu_facts_cache = cache
        return cache

    async def cpu_hardware_info(
        self, serial: str
    ) -> tuple[int | None, int | None]:
        """Return (core count, max frequency in kHz) of the device.

        Both are static hardware facts: /proc/cpuinfo counts the cores (the
        `processor` line exists on ARM and x86 alike) and the cpufreq sysfs
        holds the per-core maximum frequency in kHz - the glob is expanded by
        the DEVICE shell, so no quoting gymnastics are needed.  Cached per
        device; a missing value stays None and the UI renders a dash.
        """
        cache = self._cpu_info_cache()
        cached = cache.get(serial)
        if cached is not None:
            return cached

        cores: int | None = None
        max_freq: int | None = None
        try:
            cpuinfo = await self.shell(serial, "cat /proc/cpuinfo", timeout=10)
            cores = sum(
                1 for line in cpuinfo.splitlines() if line.lower().startswith("processor")
            ) or None
        except TransportError:
            cores = None

        try:
            freq_raw = await self.shell(
                serial,
                "cat /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq",
                timeout=10,
            )
            values = [
                int(line.strip())
                for line in freq_raw.splitlines()
                if line.strip().isdigit()
            ]
            max_freq = max(values) if values else None
            # Some emulators expose a fake cpufreq (a bare "2"): anything below
            # 100 MHz is not a real frequency, report it as unknown instead.
            if max_freq is not None and max_freq < 100_000:
                max_freq = None
        except (TransportError, ValueError):
            max_freq = None

        cache[serial] = (cores, max_freq)
        return cores, max_freq

    async def device_brand(self, serial: str) -> str | None:
        """Read ``ro.product.brand`` with a dedicated shell round trip.

        The brand never changes, so it is read once per device and cached;
        every extra round trip would slow down the polling loop.
        """
        cache = self._brand_cache()
        cached = cache.get(serial)
        if cached is not None:
            return cached
        try:
            raw = await self.shell(serial, BRAND_COMMAND, timeout=5)
        except TransportError:
            return None
        value = (raw or "").strip()
        if not value:
            return None
        cache[serial] = value
        return value

    async def close(self) -> None:
        """Release long lived resources (socket pools, ...)."""
        return None


def parse_metrics(raw: str) -> MetricsSample:
    """Parse the combined ``/proc/stat`` + ``/proc/meminfo`` payload."""
    sample = MetricsSample()
    stat_part, _, remainder = raw.partition(PROCFS_MARKER)
    mem_part, _, props_part = remainder.partition(PROPS_MARKER)

    cpu_line: str | None = None
    for line in stat_part.splitlines():
        if line.startswith("cpu "):
            cpu_line = line
            break

    total = idle = user = system = 0
    if cpu_line:
        fields = cpu_line.split()[1:]
        try:
            numbers = [int(value) for value in fields]
        except ValueError:
            numbers = []
        if numbers:
            total = sum(numbers)
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
            user = numbers[0] + numbers[1]
            system = numbers[2]
            if len(numbers) > 5:
                system += numbers[5]  # irq
            if len(numbers) > 6:
                system += numbers[6]  # softirq
            if len(numbers) > 7:
                system += numbers[7]  # steal

    sample.raw_total_jiffies = total
    sample.raw_idle_jiffies = idle
    sample.raw_user_jiffies = user
    sample.raw_system_jiffies = system

    mem_total = mem_available = None
    for line in mem_part.splitlines():
        key, _, value = line.partition(":")
        value = value.strip().split(" ")[0] if value.strip() else ""
        if not value.isdigit():
            continue
        number = int(value) * 1024
        if key == "MemTotal":
            mem_total = number
        elif key == "MemAvailable":
            mem_available = number

    if mem_total is not None:
        sample.memory_total_bytes = mem_total
        if mem_available is not None:
            sample.memory_available_bytes = mem_available
            sample.memory_used_bytes = max(0, mem_total - mem_available)
            sample.memory_percent = round(sample.memory_used_bytes / mem_total * 100, 2)

    # /proc/loadavg -> "0.00 0.02 0.13 1/1284 6607"
    load_values = props_part.strip().split()
    if len(load_values) >= 3:
        try:
            sample.load_average = (
                float(load_values[0]),
                float(load_values[1]),
                float(load_values[2]),
            )
        except ValueError:
            pass
    return sample
