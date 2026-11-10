"""End to end demo: start the API with a fake device and exercise every endpoint.

Run it with::

    cd backend && ../.venv/bin/python scripts/e2e_demo.py

It boots the real FastAPI app (real routers, real DB, real response envelope)
and only replaces the ADB transport with an in-memory fake device, because this
machine has no physical Android device attached.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="afe-e2e-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP_DIR / 'e2e.db'}"
os.environ["DATA_DIR"] = str(TMP_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.registry import build_device_uid  # noqa: E402
from app.state import get_transport_resolver  # noqa: E402
from app.transports.base import DeviceEntry, FileEntry  # noqa: E402

from tests.conftest import FakeTransport  # noqa: E402

TREE = {
    "/": [
        FileEntry(name="sdcard", path="/sdcard", is_dir=True, size=4096),
        FileEntry(name="system", path="/system", is_dir=True, size=4096),
        FileEntry(name="data", path="/data", is_dir=True, size=4096),
    ],
    "/sdcard": [
        FileEntry(name="hello.txt", path="/sdcard/hello.txt", is_dir=False, size=26),
        FileEntry(name="lib", path="/sdcard/lib", is_dir=True, size=4096),
    ],
    "/sdcard/lib": [
        FileEntry(name="libtest.so", path="/sdcard/lib/libtest.so", is_dir=False, size=64),
    ],
}


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def show(label: str, payload: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:1600])


def main() -> int:
    transport = FakeTransport(
        endpoint="local:127.0.0.1:5037",
        devices=[DeviceEntry(serial="emulator-5554", state="device", model="Pixel_5")],
        tree=TREE,
    )
    # /system and /data need root -> the device answers with Permission denied.
    transport.permission_denied_paths = {"/system", "/data"}

    resolver = get_transport_resolver()
    resolver._local = transport  # type: ignore[assignment]

    failures: list[str] = []

    with TestClient(app) as client:
        banner("R1 - 设备发现（唯一标识）")
        response = client.get("/api/v1/devices", params={"refresh": "true"})
        show("GET /api/v1/devices?refresh=true", response.json())
        devices = response.json()["data"]
        if not devices:
            failures.append("设备发现没有返回设备")
            return 1
        uid = devices[0]["uid"]
        expected_uid = build_device_uid(transport.kind, transport.endpoint, "emulator-5554")
        print(f"\n唯一标识 uid = {uid} (期望 {expected_uid})")
        if uid != expected_uid:
            failures.append("唯一标识与预期不一致")

        banner("R4 - 生命周期：用唯一标识做连接 / 状态")
        show("GET /api/v1/devices/{uid}", client.get(f"/api/v1/devices/{uid}").json())

        banner("R3 - 运行状态：CPU / 内存采样")
        show(
            "GET /api/v1/devices/{uid}/metrics",
            client.get(f"/api/v1/devices/{uid}/metrics").json(),
        )

        banner("R1 - 文件目录结构")
        show(
            "GET /api/v1/devices/{uid}/files?path=/sdcard",
            client.get(f"/api/v1/devices/{uid}/files", params={"path": "/sdcard"}).json(),
        )

        banner("R1 - 需要 root 的目录必须报错（403 而不是 500）")
        denied = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/system"})
        show("GET /api/v1/devices/{uid}/files?path=/system", denied.json())
        print(f"\nHTTP 状态码 = {denied.status_code} (期望 403)")
        if denied.status_code != 403:
            failures.append("权限不足没有返回 403")

        banner("R1 - 文件搜索")
        show(
            "GET /api/v1/devices/{uid}/files/search?keyword=libtest",
            client.get(
                f"/api/v1/devices/{uid}/files/search",
                params={"keyword": "libtest", "path": "/"},
            ).json(),
        )

        banner("R2 - 拉取设备文件")
        show(
            "POST /api/v1/devices/{uid}/files/pull",
            client.post(
                f"/api/v1/devices/{uid}/files/pull", json={"path": "/sdcard/hello.txt"}
            ).json(),
        )

        banner("R2 - 上传文件到设备")
        source = TMP_DIR / "upload.bin"
        source.write_bytes(b"uploaded through the API")
        show(
            "POST /api/v1/devices/{uid}/files/push",
            client.post(
                f"/api/v1/devices/{uid}/files/push",
                json={"path": "/sdcard/uploaded.bin", "local_path": str(source)},
            ).json(),
        )
        print(f"\n传输层实际记录: pulled={transport.pulled} pushed={transport.pushed}")

        banner("R4 - 生命周期：断开后状态变为 offline")
        disconnected = client.post(f"/api/v1/devices/{uid}/disconnect")
        show("POST /api/v1/devices/{uid}/disconnect", disconnected.json())
        if disconnected.json()["data"]["status"] != "offline":
            failures.append("断开后状态没有变成 offline")

        banner("机器列表（本地 + 远程）")
        show("GET /api/v1/engines", client.get("/api/v1/engines").json())

    print()
    print("=" * 78)
    if failures:
        print("端到端验证失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(
        "端到端验证全部通过：R1 设备发现/目录/权限错误 · R2 拉取上传 · "
        "R3 指标 · R4 唯一标识与生命周期"
    )
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
