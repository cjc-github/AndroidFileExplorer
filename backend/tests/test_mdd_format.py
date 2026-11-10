"""Regression tests for the real agent_server (MDD) wire format.

These were found by talking to a real agent_server (192.168.114.130):

1. The server wraps every command result in a Chinese envelope
   (``命令执行成功 (返回码: 0)`` / ``标准输出:`` / ``====``). The client
   must unwrap it, otherwise ``adb devices -l`` parses into one bogus
   device called "命令执行成功".
2. Android's toybox ``ls`` has no ``-N`` flag (``ls: Unknown option 'N'``).
3. toybox ``ls -l /sdcard`` describes the symlink instead of listing its
   content, so the path needs a trailing slash.
"""

from __future__ import annotations

import inspect

from app.transports.base import directory_listing_path
from app.transports.local_adb import LocalAdbTransport
from app.transports.mdd_client import unwrap_agent_output


def test_unwraps_successful_envelope():
    raw = (
        "命令执行成功 (返回码: 0)\n"
        "--------------------------------------------------\n"
        "标准输出:\n"
        "List of devices attached\n"
        "emulator-5554\tdevice\n"
        "\n"
        "=================================================="
    )
    ok, output = unwrap_agent_output(raw)
    assert ok is True
    assert output == "List of devices attached\nemulator-5554\tdevice"


def test_unwraps_failed_envelope():
    raw = (
        "命令执行失败 (返回码: 1)\n"
        "--------------------------------------------------\n"
        "错误信息:\n"
        "error: device 'emulator-5554' not found\n"
        "\n"
        "=================================================="
    )
    ok, output = unwrap_agent_output(raw)
    assert ok is False
    assert "not found" in output


def test_permission_denied_is_detected():
    raw = (
        "命令执行失败 (返回码: 1)\n"
        "--------------------------------------------------\n"
        "错误信息:\n"
        "ls: /data/: Permission denied\n"
        "\n"
        "=================================================="
    )
    ok, output = unwrap_agent_output(raw)
    assert ok is False
    assert "Permission denied" in output


def test_plain_output_passes_through():
    ok, output = unwrap_agent_output("List of devices attached")
    assert ok is True
    assert output == "List of devices attached"


def test_empty_message():
    ok, output = unwrap_agent_output("")
    assert ok is True
    assert output == ""


def test_toybox_ls_has_no_n_flag():
    """`ls -lAN` fails on toybox with "Unknown option 'N'"."""
    body = inspect.getsource(LocalAdbTransport.list_dir)
    assert '"-lAN"' not in body
    assert '"-la"' in body


def test_directory_listing_path_adds_trailing_slash():
    """toybox describes a symlink argument instead of listing it."""
    assert directory_listing_path("/sdcard") == "/sdcard/"
    assert directory_listing_path("/sdcard/") == "/sdcard/"
    assert directory_listing_path("/system") == "/system/"
    assert directory_listing_path("/") == "/"


def test_remote_transport_uses_the_helper():
    from app.transports.remote_engine import RemoteEngineTransport

    body = inspect.getsource(RemoteEngineTransport.list_dir)
    assert "directory_listing_path" in body
    assert "-lAN" not in body
