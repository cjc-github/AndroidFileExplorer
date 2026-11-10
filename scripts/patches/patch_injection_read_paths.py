"""One-shot patcher: validate every device path that reaches a shell.

`list_dir`, `pull` and `push` build `adb shell <cmd> <path>` commands.  The
path was only normalized, not validated, so the same injection that worked
for `mkdir` also worked for reading a directory or pulling a file.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "backend" / "app" / "transports" / "base.py"
REMOTE = ROOT / "backend" / "app" / "transports" / "remote_engine.py"
LOCAL = ROOT / "backend" / "app" / "transports" / "local_adb.py"


def patch(path: pathlib.Path, pairs, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            print(f"  {label}.{name} OK")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---- the import -------------------------------------------------------
base = BASE.read_text(encoding="utf-8")
if "def assert_safe_device_path" not in base:
    print("base: assert_safe_device_path is not defined yet")
    raise SystemExit(1)

# ---- remote_engine ----------------------------------------------------
patch(
    REMOTE,
    [
        (
            '''    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        normalized = normalize_device_path(path)''',
            '''    async def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        normalized = assert_safe_device_path(path)''',
            "list_dir",
        ),
        (
            '''    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        normalized = normalize_device_path(device_path)''',
            '''    async def pull(self, serial: str, device_path: str, local_path: str) -> TransferResult:
        normalized = assert_safe_device_path(device_path)''',
            "pull",
        ),
    ],
    "remote",
)

# ---- local_adb --------------------------------------------------------
local = LOCAL.read_text(encoding="utf-8")
if "assert_safe_device_path" not in local:
    print("  local: needs the import")
else:
    print("  local: import present")

patch(
    LOCAL,
    [
        (
            '''        normalized = normalize_device_path(path)''',
            '''        normalized = assert_safe_device_path(path)''',
            "list_dir",
        ),
    ],
    "local",
)

print("DONE")
