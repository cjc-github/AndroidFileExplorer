"""Unit tests for the transport layer primitives."""

from __future__ import annotations

from app.transports.base import (
    PermissionDeniedError,
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
    )
    sample = parse_metrics(raw)
    assert sample.raw_total_jiffies == 560
    assert sample.raw_idle_jiffies == 410
    assert sample.memory_total_bytes == 2048000 * 1024
    assert sample.memory_available_bytes == 1024000 * 1024
    assert sample.memory_used_bytes == 1024000 * 1024
    assert sample.memory_percent == 50.0
