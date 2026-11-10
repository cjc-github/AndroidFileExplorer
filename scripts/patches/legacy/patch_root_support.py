"""One-shot patcher: add root detection and privilege escalation to the transport.

The device list needs to show whether a device is rooted, and the file
browser needs a "尝试 root" button. On a real device:

* ``adb root`` restarts adbd with root privileges -> afterwards every shell
  command runs as ``uid=0(root)`` and protected paths become readable.
* the current identity is read from ``id`` (``uid=0(root)`` means rooted).

The patch adds ``is_rooted`` and ``try_root`` to the Transport interface and
implements them for both the local (adb subprocess) and the remote (MDD)
transport.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent / "app" / "transports"

# ---------------------------------------------------------------- base.py
base = ROOT / "base.py"
s = base.read_text(encoding="utf-8")

old_iface = '''    @abstractmethod
    async def connect(self, serial: str) -> tuple[bool, str]:
        """Establish the ADB connection of a network device."""
'''

new_iface = '''    async def is_rooted(self, serial: str) -> bool:
        """True when the device shell runs as uid 0 (root)."""
        try:
            output = await self.shell(serial, "id", timeout=5)
        except TransportError:
            return False
        return _is_root_identity(output)

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
        try:
            result = await self.adb_root(serial)
        except TransportError as exc:
            return False, str(exc)
        lowered = (result or "").lower()
        if "cannot run as root" in lowered:
            return False, result.strip() or "该设备不允许以 root 运行（生产版本）"
        if "restarting adbd as root" in lowered or "already running as root" in lowered:
            return True, result.strip() or "已以 root 模式重启 adbd"
        return False, result.strip() or "尝试 root 失败"

    @abstractmethod
    async def adb_root(self, serial: str) -> str:
        """Run ``adb root`` for one device and return its output."""

    @abstractmethod
    async def connect(self, serial: str) -> tuple[bool, str]:
        """Establish the ADB connection of a network device."""
'''

assert old_iface in s, "base interface anchor missing"
s = s.replace(old_iface, new_iface, 1)

helper = '''

def _is_root_identity(output: str) -> bool:
    """True when an ``id`` output shows uid 0 (root)."""
    text = (output or "").strip()
    if not text:
        return False
    if text.startswith("uid=0("):
        return True
    return bool(text) and "uid=0(root)" in text
'''

anchor = '''def parse_long_listing(output: str, base_path: str) -> list[FileEntry]:'''
assert anchor in s, "parse anchor missing"
s = s.replace(anchor, helper.strip() + "\n\n\n" + anchor, 1)

base.write_text(s, encoding="utf-8")
print("BASE_PATCHED")

# ----------------------------------------------------------- local_adb.py
local = ROOT / "local_adb.py"
s = local.read_text(encoding="utf-8")

old_connect = '''    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            result = self._run(["connect", serial], timeout=30)'''

new_connect = '''    async def adb_root(self, serial: str) -> str:
        def _call() -> str:
            result = self._run(["-s", serial, "root"], timeout=20)
            return (result.stdout or "") + (result.stderr or "")

        return await asyncio.to_thread(_call)

    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            result = self._run(["connect", serial], timeout=30)'''

assert old_connect in s, "local connect anchor missing"
s = s.replace(old_connect, new_connect, 1)
local.write_text(s, encoding="utf-8")
print("LOCAL_PATCHED")

# -------------------------------------------------------- remote_engine.py
remote = ROOT / "remote_engine.py"
s = remote.read_text(encoding="utf-8")

old_remote_connect = '''    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            try:
                output = self._execute(f"adb connect {shlex.quote(serial)}", timeout=30)'''

new_remote_connect = '''    async def adb_root(self, serial: str) -> str:
        def _call() -> str:
            return self._execute(f"adb -s {shlex.quote(serial)} root", timeout=20)

        return await asyncio.to_thread(_call)

    async def connect(self, serial: str) -> tuple[bool, str]:
        def _call() -> tuple[bool, str]:
            try:
                output = self._execute(f"adb connect {shlex.quote(serial)}", timeout=30)'''

assert old_remote_connect in s, "remote connect anchor missing"
s = s.replace(old_remote_connect, new_remote_connect, 1)
remote.write_text(s, encoding="utf-8")
print("REMOTE_PATCHED")
