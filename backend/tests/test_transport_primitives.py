"""Unit tests for the transport layer primitives."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.transports.base import (
    DeviceOfflineError,
    PermissionDeniedError,
    TransferResult,
    Transport,
    classify_output,
    join_device_path,
    normalize_device_path,
    parent_device_path,
    parse_long_listing,
    parse_metrics,
    path_parts,
)
from app.transports.local_adb import parse_adb_devices

LS_OUTPUT = """total 24
drwxrwx--- 2 root   u0_a12    4096 2024-01-01 12:00 obb
-rw-rw-rw- 1 root   root     1234 2024-01-02 13:45 lib.so
lrwxrwxrwx 1 root   root       11 2024-01-03 09:00 link -> /sdcard/target
"""

BUSYBOX_LS_OUTPUT = """total 12
drwxrwxr-x 2 root root 4096 Jan  4 09:30 dir
-rw-rw-r-- 1 root root  123 Jan  5  2023 old.txt
"""


def test_parse_long_listing_extracts_entries():
    entries = parse_long_listing(LS_OUTPUT, "/sdcard")
    names = {entry.name for entry in entries}
    assert names == {"obb", "lib.so", "link"}
    assert "." not in names and ".." not in names

    by_name = {entry.name: entry for entry in entries}
    assert by_name["obb"].is_dir is True
    assert by_name["obb"].size == 4096
    assert by_name["lib.so"].is_dir is False
    assert by_name["lib.so"].size == 1234
    assert by_name["lib.so"].path == "/sdcard/lib.so"
    assert by_name["link"].is_link is True
    assert by_name["link"].link_target == "/sdcard/target"


def test_parse_long_listing_reads_toybox_modified_time():
    by_name = {entry.name: entry for entry in parse_long_listing(LS_OUTPUT, "/sdcard")}
    assert by_name["obb"].modified_at == datetime(2024, 1, 1, 12, 0)
    assert by_name["lib.so"].modified_at == datetime(2024, 1, 2, 13, 45)
    assert by_name["link"].modified_at == datetime(2024, 1, 3, 9, 0)


def test_parse_long_listing_reads_busybox_modified_time():
    by_name = {
        entry.name: entry
        for entry in parse_long_listing(BUSYBOX_LS_OUTPUT, "/sdcard")
    }
    old = by_name["old.txt"]
    assert old.modified_at is not None
    assert (old.modified_at.year, old.modified_at.month, old.modified_at.day) == (2023, 1, 5)
    # No year in the row -> completed with the current one.
    fresh = by_name["dir"]
    assert fresh.modified_at is not None
    assert fresh.modified_at.year == datetime.now().year
    assert (fresh.modified_at.hour, fresh.modified_at.minute) == (9, 30)


def test_parse_long_listing_keeps_rows_without_a_time_column():
    """toybox omits the time for files older than ~6 months.

    The name then sits one column earlier; if the parser kept the old
    offset the whole row would be dropped and the file would vanish from
    the listing.
    """
    output = """total 8
drwxr-xr-x 1 root root 4096 2024-02-08 ancient
-rw-r--r-- 1 root root  100 2024-02-08 ancient.txt
"""
    entries = parse_long_listing(output, "/x")
    assert {entry.name for entry in entries} == {"ancient", "ancient.txt"}
    by_name = {entry.name: entry for entry in entries}
    assert by_name["ancient"].is_dir is True
    assert by_name["ancient.txt"].size == 100
    assert by_name["ancient"].modified_at == datetime(2024, 2, 8)


def test_parse_long_listing_accepts_a_time_with_seconds():
    output = "-rw-r--r-- 1 root root  100 2024-02-08 07:23:45 withsec\n"
    entry = parse_long_listing(output, "/x")[0]
    assert entry.name == "withsec"
    assert entry.modified_at == datetime(2024, 2, 8, 7, 23, 45)


def test_parse_long_listing_raises_on_permission_error():
    try:
        parse_long_listing("ls: /data: Permission denied", "/data")
    except PermissionDeniedError as exc:
        assert "Permission denied" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected PermissionDeniedError")


def test_classify_output_maps_markers():
    assert isinstance(classify_output("ls: /x: Permission denied"), PermissionDeniedError)
    assert classify_output("total 0\n-rw-rw-rw- 1 root root 0 x") is None


def test_a_vanished_device_is_offline():
    """adb reports a gone device with the serial in between the words.

    "error: device 'emulator-5554' not found" does NOT contain the plain
    "device not found", so it needs its own pattern - otherwise a
    disconnected device is not reported as offline.
    """
    assert isinstance(
        classify_output("error: device 'emulator-5554' not found"),
        DeviceOfflineError,
    )


def test_a_shell_tool_not_found_is_not_offline():
    """The same words in a harmless shell answer must not mark a device offline."""
    assert classify_output("sh: missing-tool: not found") is None


def test_path_helpers():
    assert normalize_device_path("") == "/"
    assert normalize_device_path("/sdcard/") == "/sdcard"
    assert normalize_device_path("sdcard//Download/") == "/sdcard/Download"
    assert parent_device_path("/sdcard/Download") == "/sdcard"
    assert parent_device_path("/sdcard") == "/"
    assert join_device_path("/", "a.txt") == "/a.txt"
    assert join_device_path("/sdcard", "a.txt") == "/sdcard/a.txt"
    assert path_parts("/sdcard/Android") == [
        ("sdcard", "/sdcard"),
        ("Android", "/sdcard/Android"),
    ]
    assert path_parts("/") == []


def test_parse_adb_devices_handles_states():
    output = """List of devices attached
emulator-5554\tdevice product:sdk model:Pixel_5 device:generic transport_id:1
192.168.1.5:5555\tdevice product:x model:Y device:Z transport_id:2
unauthorized-xyz\tunauthorized
offline-dev\toffline
"""
    entries = parse_adb_devices(output)
    assert len(entries) == 4
    by_serial = {entry.serial: entry for entry in entries}
    assert by_serial["emulator-5554"].model == "Pixel_5"
    assert by_serial["emulator-5554"].is_online is True
    assert by_serial["192.168.1.5:5555"].is_network_device is True
    assert by_serial["unauthorized-xyz"].is_online is False
    assert by_serial["offline-dev"].state == "offline"


def test_parse_metrics_reads_procfs():
    raw = (
        "cpu  100 20 30 400 10 0 0 0 0 0\n"
        "cpu0 100 20 30 400 10 0 0 0 0 0\n"
        "intr 12345\n"
        "---AFE---\n"
        "MemTotal:        2048000 kB\n"
        "MemFree:          512000 kB\n"
        "MemAvailable:    1024000 kB\n"
        "---AFEPROPS---\n"
        "0.00 0.02 0.13 1/1284 6607\n"
        "---AFEFREQ---\n"
        "1800000\n"
        "2400000\n"
        "---AFESTORAGEDATA---\n"
        "Filesystem       1K-blocks     Used Available Use% Mounted on\n"
        "/dev/block/dm-1    1000000   400000    600000  40% /data\n"
        "---AFESTORAGESDCARD---\n"
        "Filesystem       1K-blocks     Used Available Use% Mounted on\n"
        "/dev/block/dm-2     500000   100000    400000  20% /storage/emulated\n"
    )
    sample = parse_metrics(raw)
    assert sample.raw_total_jiffies == 560
    assert sample.raw_idle_jiffies == 410
    assert sample.memory_total_bytes == 2048000 * 1024
    assert sample.memory_available_bytes == 1024000 * 1024
    assert sample.memory_used_bytes == 1024000 * 1024
    assert sample.memory_percent == 50.0
    assert sample.load_average == (0.0, 0.02, 0.13)
    assert sample.cpu_current_freq_khz == 2100000
    assert [item.path for item in sample.storage] == ["/data", "/sdcard"]
    assert sample.storage[0].total_bytes == 1000000 * 1024
    assert sample.storage[0].used_bytes == 400000 * 1024
    assert sample.storage[1].available_bytes == 400000 * 1024


class _CountingTransport(Transport):
    """Records every shell call so the root cache can be verified."""

    kind = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def endpoint(self) -> str:
        return "counting"

    async def list_devices(self) -> list:
        return []

    async def shell(self, serial: str, command: str, timeout: float | None = None) -> str:
        self.calls.append(command)
        return "uid=2000(shell) gid=2000(shell)"

    async def list_dir(self, serial: str, path: str) -> list:
        return []

    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        return TransferResult(ok=True, path=device_path)

    async def push(self, serial: str, local_path: str, device_path: str) -> TransferResult:
        return TransferResult(ok=True, path=device_path)

    async def connect(self, serial: str) -> tuple[bool, str]:
        return True, ""

    async def disconnect(self, serial: str) -> tuple[bool, str]:
        return True, ""


def test_is_rooted_result_is_cached_within_the_ttl():
    transport = _CountingTransport()
    assert asyncio.run(transport.is_rooted("s")) is False
    assert asyncio.run(transport.is_rooted("s")) is False
    assert transport.calls.count("id") == 1


def test_set_root_drops_the_cached_root_flag():
    transport = _CountingTransport()
    asyncio.run(transport.is_rooted("s"))
    asyncio.run(transport.set_root("s", True))
    asyncio.run(transport.is_rooted("s"))
    assert transport.calls.count("id") == 2
