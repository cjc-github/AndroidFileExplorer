"""Regression test for the local adb transport.

The local transport can only be verified on a machine that actually has a
device attached, so the test drives it with a fake ``adb`` executable: the
parsing, the command building and the device list are checked without any
hardware.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from app.transports.local_adb import LocalAdbTransport


FAKE_ADB_SH = """#!/bin/bash
case "$*" in
  "devices -l")
    echo "List of devices attached"
    printf 'FAKE123\\tdevice product:fake model:Fake_X device:fake transport_id:1\\n'
    ;;
  "devices")
    echo "List of devices attached"
    printf 'FAKE123\\tdevice\\n'
    ;;
  *)
    echo "FAKE-OK: $*"
    ;;
esac
"""

FAKE_ADB_CMD = """@echo off
echo %* | findstr /C:"devices" >nul
if not errorlevel 1 (
  echo List of devices attached
  echo FAKE123	device product:fake model:Fake_X device:fake transport_id:1
  exit /b 0
)
echo FAKE-OK: %*
"""


@pytest.fixture
def fake_adb(tmp_path) -> str:
    """A fake ``adb`` executable for the current platform.

    The local transport spawns adb as a plain executable (no shell), so the
    fixture has to provide something Windows can run: a bash script raises
    ``WinError 193`` there.  A ``.cmd`` file is launched through ``cmd.exe``
    by ``subprocess`` on Windows, so it works without a shell wrapper.
    """
    if os.name == "nt":
        path = tmp_path / "adb.cmd"
        path.write_text(FAKE_ADB_CMD, encoding="utf-8")
        return str(path)

    path = tmp_path / "adb"
    path.write_text(FAKE_ADB_SH, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def run(coro):
    return asyncio.run(coro)


def test_the_local_transport_finds_the_attached_devices(fake_adb):
    transport = LocalAdbTransport(adb_path=fake_adb)
    devices = run(transport.list_devices())
    assert len(devices) == 1
    assert devices[0].serial == "FAKE123"
    assert devices[0].state == "device"
    assert devices[0].model == "Fake_X"


def test_the_local_transport_runs_a_shell_command(fake_adb):
    transport = LocalAdbTransport(adb_path=fake_adb)
    output = run(transport.shell("FAKE123", "id"))
    assert "FAKE-OK" in output


def test_a_missing_adb_is_reported_clearly(tmp_path):
    missing = tmp_path / "no-such-adb"
    with pytest.raises((RuntimeError, FileNotFoundError, OSError)):
        transport = LocalAdbTransport(adb_path=str(missing))
        run(transport.list_devices())
