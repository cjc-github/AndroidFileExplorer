"""Frontend <-> backend live check.

Boots the real backend (with an in-memory fake device) and the real Vite dev
server, then drives the frontend through its own HTTP proxy so the whole
chain (browser -> vite proxy -> backend -> transport) is exercised.

Run it with::

    cd backend && ../.venv/bin/python scripts/frontend_live_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="afe-fe-live-"))
# The Vite dev server proxies /api to http://127.0.0.1:8010 (frontend/vite.config.ts),
# so the backend MUST listen on that port for the proxy chain to work.
PROXY_TARGET_PORT = 8010
BACKEND_PORT = PROXY_TARGET_PORT
FRONTEND_PORT = 18022
FRONTEND_BASE = f"http://127.0.0.1:{FRONTEND_PORT}"

SERVER_SCRIPT = TMP_DIR / "server.py"
SERVER_SCRIPT.write_text(
    f'''
import os, sys
sys.path.insert(0, {str(BACKEND_ROOT)!r})
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///{TMP_DIR / 'fe.db'}"
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
    def endpoint(self):
        return self._endpoint
    async def list_devices(self):
        return [DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")]
    async def shell(self, serial, command, timeout=None):
        return ""
    async def list_dir(self, serial, path):
        return list(self.tree.get(path, []))
    async def pull(self, serial, device_path, local_path):
        open(local_path, "wb").write(b"hello from the fake device")
        return TransferResult(ok=True, path=device_path, size=26, stored_at=local_path)
    async def push(self, serial, local_path, device_path):
        return TransferResult(ok=True, path=device_path, size=os.path.getsize(local_path), stored_at=device_path)
    async def connect(self, serial):
        return True, "connected"
    async def disconnect(self, serial):
        return True, "disconnected"
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
uvicorn.run(app, host="127.0.0.1", port={BACKEND_PORT}, log_level="warning")
''',
    encoding="utf-8",
)


def wait_for_port(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def http(method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    url = f"{FRONTEND_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError as exc:
        return 0, str(exc)


def main() -> int:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    failures: list[str] = []

    backend = subprocess.Popen(
        [str(python), str(SERVER_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    nvm_dir = os.path.expanduser("~/.nvm")
    env = dict(os.environ)
    env["NVM_DIR"] = nvm_dir
    frontend = subprocess.Popen(
        [
            "bash",
            "-lc",
            f"source {nvm_dir}/nvm.sh && nvm use 22 && "
            f"npm run dev -- --port {FRONTEND_PORT} --host 127.0.0.1",
        ],
        cwd=str(PROJECT_ROOT / "frontend"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        if not wait_for_port(BACKEND_PORT):
            print("后端未能启动")
            return 1
        if not wait_for_port(FRONTEND_PORT, timeout=90):
            print("前端 dev server 未能启动")
            if frontend.stdout:
                print(frontend.stdout.read()[-3000:])
            return 1

        print("=" * 78)
        print("  前端 -> vite proxy -> 后端 真实链路验证")
        print("=" * 78)

        status, body = http("GET", "/")
        print(f"\n--- GET / (前端首页) -> HTTP {status} ---")
        html = body if isinstance(body, str) else json.dumps(body)
        print(html[:400])
        if status != 200 or 'id="root"' not in html:
            failures.append("前端首页没有正常返回")

        status, body = http("GET", "/api/v1/devices?refresh=true")
        print(f"\n--- GET /api/v1/devices?refresh=true (经 vite 代理) -> HTTP {status} ---")
        print(body[:700] if isinstance(body, str) else body)
        payload = json.loads(body) if isinstance(body, str) else {}
        devices = payload.get("data") or []
        if not devices:
            failures.append("经代理没有拿到设备")
        else:
            uid = devices[0]["uid"]
            print(f"\n设备 uid = {uid}")

            status, body = http("GET", f"/api/v1/devices/{uid}/metrics")
            print(f"\n--- GET /api/v1/devices/{{uid}}/metrics -> HTTP {status} ---")
            print(body[:500] if isinstance(body, str) else body)
            if status != 200:
                failures.append("指标接口经代理失败")

            status, body = http("GET", f"/api/v1/devices/{uid}/files?path=/sdcard")
            print(f"\n--- GET /api/v1/devices/{{uid}}/files?path=/sdcard -> HTTP {status} ---")
            print(body[:700] if isinstance(body, str) else body)
            if status != 200:
                failures.append("文件列表经代理失败")
    finally:
        for process in (frontend, backend):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    print()
    print("=" * 78)
    if failures:
        print("前端联调验证失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("前端联调通过：vite dev server 代理 /api 到后端，设备发现 / 指标 / 文件列表全部可达")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
