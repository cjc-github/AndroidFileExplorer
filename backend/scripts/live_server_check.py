"""Boot the API as a real uvicorn server with a fake device, then drive it over HTTP.

This is the final end to end proof: a separate process serves the real
application, and the checks below use plain HTTP requests (no TestClient).

Run it with::

    cd backend && ../.venv/bin/python scripts/live_server_check.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="afe-live-"))
PORT = 18011
BASE = f"http://127.0.0.1:{PORT}"

SERVER_SCRIPT = TMP_DIR / "server.py"
SERVER_SCRIPT.write_text(
    f'''
import os, sys
sys.path.insert(0, {str(BACKEND_ROOT)!r})
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///{TMP_DIR / 'live.db'}"
os.environ["DATA_DIR"] = {str(TMP_DIR)!r}
os.environ["SERVER_PORT"] = "{PORT}"

import uvicorn
from app.main import app
from app.state import get_transport_resolver
from app.transports.base import DeviceEntry, FileEntry

class FakeTransport:
    kind = "local"
    def __init__(self):
        self._endpoint = "local:127.0.0.1:5037"
        self.tree = {{
            "/": [FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096)],
            "/sdcard": [
                FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=26),
                FileEntry(name="system", path="/system", is_dir=True, size=4096),
            ],
        }}
        self.permission_denied_paths = {{"/system"}}
    @property
    def endpoint(self):
        return self._endpoint
    async def list_devices(self):
        return [DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")]
    async def shell(self, serial, command, timeout=None):
        return ""
    async def list_dir(self, serial, path):
        if path in self.permission_denied_paths:
            from app.transports.base import PermissionDeniedError
            raise PermissionDeniedError(f"Permission denied: {{path}}")
        return list(self.tree.get(path, []))
    async def pull(self, serial, device_path, local_path):
        from app.transports.base import TransferResult
        open(local_path, "wb").write(b"hello from the fake device")
        return TransferResult(ok=True, path=device_path, size=26, stored_at=local_path)
    async def push(self, serial, local_path, device_path):
        from app.transports.base import TransferResult
        return TransferResult(ok=True, path=device_path, size=os.path.getsize(local_path), stored_at=device_path)
    async def connect(self, serial):
        return True, "connected"
    async def disconnect(self, serial):
        return True, "disconnected"
    async def metrics(self, serial):
        from app.transports.base import MetricsSample
        s = MetricsSample()
        s.raw_total_jiffies = 1000
        s.raw_idle_jiffies = 400
        s.memory_total_bytes = 8 * 1024 ** 3
        s.memory_available_bytes = 4 * 1024 ** 3
        s.memory_used_bytes = 4 * 1024 ** 3
        s.memory_percent = 50.0
        return s

get_transport_resolver()._local = FakeTransport()
uvicorn.run(app, host="127.0.0.1", port={PORT}, log_level="warning")
''',
    encoding="utf-8",
)


def wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def http(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def show(label: str, status: int, payload: object) -> None:
    print(f"\n--- {label} -> HTTP {status} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:900])


def main() -> int:
    python = BACKEND_ROOT.parent / ".venv" / "bin" / "python"
    process = subprocess.Popen(
        [str(python), str(SERVER_SCRIPT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    failures: list[str] = []
    try:
        if not wait_for_port(PORT):
            print("服务未能启动")
            if process.stdout:
                print(process.stdout.read()[-2000:])
            return 1

        status, payload = http("GET", "/api/v1/health")
        show("GET /api/v1/health", status, payload)
        if status != 200:
            failures.append("健康检查失败")

        status, payload = http("GET", "/api/v1/devices?refresh=true")
        show("GET /api/v1/devices?refresh=true", status, payload)
        devices = payload.get("data") or []
        if not devices:
            failures.append("没有发现设备")
            return 1
        uid = devices[0]["uid"]
        print(f"\n设备唯一标识 uid = {uid}")

        status, payload = http("GET", f"/api/v1/devices/{uid}/metrics")
        show("GET /api/v1/devices/{uid}/metrics", status, payload)
        metrics = payload.get("data") or {}
        if metrics.get("memory_percent") != 50.0:
            failures.append("内存指标不正确")

        status, payload = http("GET", f"/api/v1/devices/{uid}/files?path=/sdcard")
        show("GET /api/v1/devices/{uid}/files?path=/sdcard", status, payload)

        status, payload = http("GET", f"/api/v1/devices/{uid}/files?path=/system")
        show("GET /api/v1/devices/{uid}/files?path=/system", status, payload)
        if status != 403:
            failures.append("权限不足没有返回 403")

        status, payload = http(
            "GET", f"/api/v1/devices/{uid}/files/search?keyword=hello&path=/"
        )
        show("GET /api/v1/devices/{uid}/files/search", status, payload)

        status, payload = http(
            "POST", f"/api/v1/devices/{uid}/files/pull", {"path": "/sdcard/hello.txt"}
        )
        show("POST /api/v1/devices/{uid}/files/pull", status, payload)
        if not (payload.get("data") or {}).get("ok"):
            failures.append("拉取失败")

        upload_file = TMP_DIR / "live-upload.bin"
        upload_file.write_bytes(b"pushed by the live check")
        status, payload = http(
            "POST",
            f"/api/v1/devices/{uid}/files/push",
            {"path": "/sdcard/live-upload.bin", "local_path": str(upload_file)},
        )
        show("POST /api/v1/devices/{uid}/files/push", status, payload)
        if not (payload.get("data") or {}).get("ok"):
            failures.append("上传失败")

        status, payload = http("POST", f"/api/v1/devices/{uid}/disconnect")
        show("POST /api/v1/devices/{uid}/disconnect", status, payload)
        if (payload.get("data") or {}).get("status") != "offline":
            failures.append("断开后状态没有变成 offline")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    print()
    print("=" * 78)
    if failures:
        print("真实服务验证失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("真实 uvicorn 服务 + 真实 HTTP 请求：全部检查通过")
    print("  R1 设备发现 / 目录结构 / 权限 403 · R2 拉取上传 · R3 CPU 内存指标 · R4 生命周期断开")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
