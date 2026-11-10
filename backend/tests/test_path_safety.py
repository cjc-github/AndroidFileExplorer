"""Regression tests for the command injection boundary.

`adb shell` re-parses the command on the DEVICE, where the quoting of the
agent side is already gone.  A path like

    /sdcard/Download/x$(touch /sdcard/Download/PWNED)

therefore used to run the substitution on the device - the platform was a
remote code execution on every Android device it manages.

Every device path must be rejected when it contains shell metacharacters.
"""

from __future__ import annotations

import pytest

from app.transports.base import (
    UnsafePathError,
    assert_safe_device_path,
    normalize_device_path,
)


@pytest.mark.parametrize(
    "payload",
    [
        "/sdcard/Download/x$(touch /sdcard/Download/PWNED)",
        "/sdcard/Download/a;id",
        "/sdcard/Download/`id`",
        "/sdcard/Download/a|id",
        "/sdcard/Download/a&b",
        "/sdcard/Download/a>b",
        "/sdcard/Download/a'b",
        '/sdcard/Download/a"b',
        "/sdcard/Download/a\nb",
        "/sdcard/Download/a`b`",
        "/sdcard/Download/$(id)",
        "/sdcard/Download/a$(id)",
    ],
)
def test_shell_metacharacters_are_rejected(payload: str):
    with pytest.raises(UnsafePathError):
        assert_safe_device_path(payload)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/sdcard",
        "/sdcard/Download",
        "/data/local/tmp/lib.so",
        "/sdcard/My File.txt",
        "/sdcard/file-name_1.log",
        "/sdcard/中文目录",
    ],
)
def test_ordinary_paths_are_allowed(path: str):
    assert assert_safe_device_path(path) == normalize_device_path(path)


def test_the_error_names_the_offending_character():
    with pytest.raises(UnsafePathError) as info:
        assert_safe_device_path("/sdcard/a;id")
    assert ";" in str(info.value)


def test_the_unsafe_path_error_is_a_value_error():
    """The API layer catches ValueError and answers 400."""
    assert issubclass(UnsafePathError, ValueError)
