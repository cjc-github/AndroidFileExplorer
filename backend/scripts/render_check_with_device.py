"""Boot the backend with one fake device, then run the browser render checks.

This closes the loop of the "blank page" incident: the frontend is verified
in a real browser with a device present, not just built.

Usage::

    cd backend && ../.venv/bin/python scripts/render_check_with_device.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="afe-render-"))
BACKEND_PORT = 8010
FRONTEND_PORT = 10012
DEVICE_UID = "95fad2083bf7161a"

SERVER_SCRIPT = TMP_DIR / "server.py"
SERVER_SCRIPT.write_text(
    f'''
import os, sys
sys.path.insert(0, {str(BACKEND_ROOT)!r})
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///{TMP_DIR / 'db.sqlite'}"
os.environ["DATA_DIR"] = {str(TMP_DIR)!r}

import uvicorn
from app.main import app
from app.state import get_transport_resolver
from app.transports.base import DeviceEntry, FileEntry, MetricsSample, TransferResult

class FakeTransport:
    kind = "local"
    def __init__(self):
        self._endpoint = "local:127.0.0.1:5037"
        self.tree = {{
            "/": [FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096)],
            "/sdcard": [FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=26)],
        }}
    @property
    def endpoint(self): return self._endpoint
    async def list_devices(self):
        return [DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")]
    async def shell(self, serial, command, timeout=None): return ""
    async def list_dir(self, serial, path): return list(self.tree.get(path, []))
    async def pull(self, serial, device_path, local_path):
        open(local_path, "wb").write(b"hello")
        return TransferResult(ok=True, path=device_path, size=5, stored_at=local_path)
    async def push(self, serial, local_path, device_path):
        return TransferResult(ok=True, path=device_path, size=1, stored_at=device_path)
    async def connect(self, serial): return True, "connected"
    async def disconnect(self, serial): return True, "disconnected"
    async def metrics(self, serial):
        s = MetricsSample()
        s.raw_total_jiffies = 1000
        s.raw_idle_jiffies = 400
        s.memory_total_bytes = 8 * 1024 ** 3
        s.memory_available_bytes = 4 * 1024 ** 3
        s.memory_used_bytes = 4 * 1024 ** 3
        s.memory_percent = 50.0
        return s

get_transport_resolver()._local = FakeTransport()
uvicorn.run(app, host="0.0.0.0", port={BACKEND_PORT}, log_level="warning")
''',
    encoding="utf-8",
)


def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["bash", "-lc", f"(exec 3<>/dev/tcp/127.0.0.1/{port}) 2>/dev/null"],
            check=False,
        )
        if result.returncode == 0:
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    render_check = PROJECT_ROOT / "scripts" / "render-check.sh"

    # Stop the real backend so the fake one can take the port.
    subprocess.run(["pkill", "-f", "app.main"], check=False)
    time.sleep(2)

    backend = subprocess.Popen(
        [str(python), str(SERVER_SCRIPT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_port(BACKEND_PORT):
            print("[error] 假设备后端未启动")
            return 1
        print(f"[ok] 假设备后端已启动 :{BACKEND_PORT}")
        time.sleep(2)

        failures = 0

        for slug, url, extra in (
            ("machines", f"http://127.0.0.1:{FRONTEND_PORT}/machines", []),
            ("devices", f"http://127.0.0.1:{FRONTEND_PORT}/devices", ["--expect-device"]),
            ("picker", f"http://127.0.0.1:{FRONTEND_PORT}/files", ["--expect-device"]),
            ("files", f"http://127.0.0.1:{FRONTEND_PORT}/devices/{DEVICE_UID}/files", []),
            ("settings", f"http://127.0.0.1:{FRONTEND_PORT}/settings", []),
        ):
            print(f"\n########## {slug} ##########")
            result = subprocess.run(
                ["bash", str(render_check), url, "--page", slug, *extra],
                check=False,
            )
            failures += result.returncode

        return 1 if failures else 0
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()


if __name__ == "__main__":
    raise SystemExit(main())
