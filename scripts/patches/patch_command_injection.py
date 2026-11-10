"""One-shot patcher: close the command injection hole.

`shlex.quote` protects the shell of the MACHINE that runs the agent, but the
command is handed to `adb shell`, which runs it again on the DEVICE with the
quotes stripped.  A path like

    /sdcard/Download/x$(touch /sdcard/Download/PWNED)

therefore executed the substitution on the device: the platform turned into a
remote code execution on every Android device it manages.

The fix is a whitelist: a device path may only contain characters that are
harmless in a shell.  Android file names never need the shell metacharacters.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "backend" / "app" / "transports" / "base.py"
source = BASE.read_text(encoding="utf-8")

OLD_NORMALIZE = '''def normalize_device_path(path: str) -> str:
    """Normalize a device side path to an absolute POSIX path without trailing slash."""
    if not path or path == "/":
        return "/"
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    normalized = posixpath.normpath(cleaned)
    return normalized if normalized.startswith("/") else "/" + normalized'''

NEW_NORMALIZE = '''#: Characters that must never appear in a device path.  They are shell
#: metacharacters, and `adb shell` re-parses the command on the device where
#: the quoting of the agent side is already gone.
FORBIDDEN_PATH_CHARS = set(";|&$`<>()\\"'\\n\\r\\t*?[]{}!#~%^,")


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
    return normalized if normalized.startswith("/") else "/" + normalized'''

if OLD_NORMALIZE in source:
    source = source.replace(OLD_NORMALIZE, NEW_NORMALIZE, 1)
    print("NORMALIZE_PATCHED")
else:
    print("ANCHOR MISSING: normalize_device_path")
    raise SystemExit(1)

# The three operations that build a shell command out of a user supplied path.
REPLACEMENTS = [
    (
        '''        await self.shell(serial, f"mkdir -p {shlex.quote(normalized)}", timeout=20)''',
        '''        await self.shell(serial, f"mkdir -p {shlex.quote(normalized)}", timeout=20)''',
    ),
]

# mkdir / rename / remove: validate before the command is built.
OLD_MKDIR = '''        normalized = normalize_device_path(path)
        if normalized == "/":
            raise ValueError("不能创建根目录")'''
NEW_MKDIR = '''        normalized = assert_safe_device_path(path)
        if normalized == "/":
            raise ValueError("不能创建根目录")'''

OLD_RENAME = '''        source_path = normalize_device_path(source)
        target_path = normalize_device_path(target)'''
NEW_RENAME = '''        source_path = assert_safe_device_path(source)
        target_path = assert_safe_device_path(target)'''

OLD_REMOVE = '''        normalized = normalize_device_path(path)
        if normalized == "/":
            raise ValueError("不能删除根目录")'''
NEW_REMOVE = '''        normalized = assert_safe_device_path(path)
        if normalized == "/":
            raise ValueError("不能删除根目录")'''

for old, new, label in (
    (OLD_MKDIR, NEW_MKDIR, "mkdir"),
    (OLD_RENAME, NEW_RENAME, "rename"),
    (OLD_REMOVE, NEW_REMOVE, "remove"),
):
    if old in source:
        source = source.replace(old, new, 1)
        print(f"{label.upper()}_PATCHED")
    else:
        print(f"ANCHOR MISSING: {label}")
        raise SystemExit(1)

BASE.write_text(source, encoding="utf-8")
print("DONE")
