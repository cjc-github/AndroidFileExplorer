"""End to end acceptance against a REAL remote machine and a REAL device.

Unlike ``e2e_demo.py`` (in-memory fake device) and ``live_server_check.py``
(fake device behind a real uvicorn), this script talks to an actual
``agent_server`` on a real host with a real Android device attached.

Usage::

    cd backend && ../.venv/bin/python scripts/real_device_check.py \\
        --host 192.168.114.130 --port 12345
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

TMP_DIR = Path(tempfile.mkdtemp(prefix="afe-real-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP_DIR / 'real.db'}"
os.environ["DATA_DIR"] = str(TMP_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.registry import build_device_uid  # noqa: E402
from app.state import get_transport_resolver  # noqa: E402
from app.transports.remote_engine import RemoteEngineTransport  # noqa: E402


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(f"  {title}")
    print("=" * 78)


def show(label: str, payload: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:1200])


async def main_async(host: str, port: int) -> int:
    failures: list[str] = []

    transport = RemoteEngineTransport(host, port, connect_timeout=8, command_timeout=40)

    banner("R1 - 发现远程机器上的真实设备")
    devices = await transport.list_devices()
    print(f"  {host}:{port} -> {len(devices)} 台设备")
    for device in devices:
        print(f"    serial={device.serial} state={device.state} model={device.model}")
    if not devices:
        failures.append("远程机器上没有设备")
        return 1

    serial = devices[0].serial
    uid = build_device_uid(transport.kind, transport.endpoint, serial)
    print(f"\n  唯一标识 uid = {uid}")

    resolver = get_transport_resolver()
    resolver.register_engine(9999, "remote", host, port)

    with TestClient(app) as client:
        # Register the machine so the API resolves it by engine id.
        engine = client.post(
            "/api/v1/engines",
            json={"name": f"真实机器 {host}", "host": host, "port": port, "kind": "remote"},
        ).json()["data"]
        print(f"  机器登记 engine_id={engine['id']} device_count={engine['device_count']}")

        # Point the device row at that engine.
        from app.db.session import async_session_maker
        from app.services.registry import DeviceRegistry

        async def _seed() -> None:
            async with async_session_maker() as session:
                registry = DeviceRegistry(session)
                await registry.upsert_device(transport, devices[0], engine["id"])

        await _seed()

        banner("R3 - 真实设备的 CPU / 内存")
        first = client.get(f"/api/v1/devices/{uid}/metrics")
        show("GET /api/v1/devices/{uid}/metrics (第 1 次)", first.json())
        await asyncio.sleep(2)
        second = client.get(f"/api/v1/devices/{uid}/metrics")
        show("GET /api/v1/devices/{uid}/metrics (第 2 次，有基线)", second.json())
        metrics = second.json().get("data") or {}
        if metrics.get("cpu_percent") is None:
            failures.append("第二次采样仍无 CPU 基线")
        if metrics.get("memory_total_bytes") in (None, 0):
            failures.append("内存总量为空")

        banner("R1 - 真实设备的目录结构")
        listing = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/sdcard"})
        show("GET .../files?path=/sdcard", listing.json())
        if listing.status_code != 200:
            failures.append(f"/sdcard 列举失败: {listing.status_code}")

        banner("R1 - 需要 root 的目录必须 403")
        denied = client.get(f"/api/v1/devices/{uid}/files", params={"path": "/data"})
        show("GET .../files?path=/data", denied.json())
        print(f"\n  HTTP = {denied.status_code} (期望 403)")
        if denied.status_code != 403:
            failures.append("权限不足没有返回 403")

        banner("R2 - 上传到真实设备")
        source = TMP_DIR / "real-upload.txt"
        source.write_text(f"uploaded by the real device check {os.getpid()}")
        pushed = client.post(
            f"/api/v1/devices/{uid}/files/push",
            json={"path": "/sdcard/Download/real-check.txt", "local_path": str(source)},
        )
        show("POST .../files/push", pushed.json())
        if not (pushed.json().get("data") or {}).get("ok"):
            failures.append("上传失败")

        banner("R2 - 拉取真实设备文件")
        pulled = client.post(
            f"/api/v1/devices/{uid}/files/pull",
            json={"path": "/sdcard/Download/real-check.txt"},
        )
        show("POST .../files/pull", pulled.json())
        if not (pulled.json().get("data") or {}).get("ok"):
            failures.append("拉取失败")

        banner("R1 - 在真实设备上搜索")
        found = client.get(
            f"/api/v1/devices/{uid}/files/search",
            params={"keyword": "real-check", "path": "/sdcard"},
        )
        show("GET .../files/search?keyword=real-check", found.json())
        items = (found.json().get("data") or {}).get("items") or []
        if not items:
            failures.append("真实设备上没搜到刚上传的文件")

        banner("R4 - 生命周期状态")
        detail = client.get(f"/api/v1/devices/{uid}")
        show("GET /api/v1/devices/{uid}", detail.json())
        if (detail.json().get("data") or {}).get("status") != "online":
            failures.append("设备状态不是 online")

    print()
    print("=" * 78)
    if failures:
        print("真实设备验收失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(
        f"真实设备验收通过（{host}:{port} / {serial}）："
        "R1 设备发现·目录·权限403 · R2 上传下载 · R3 CPU内存 · R4 唯一标识与状态"
    )
    print("=" * 78)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Real device acceptance check")
    parser.add_argument("--host", default="192.168.114.130")
    parser.add_argument("--port", type=int, default=12345)
    args = parser.parse_args()
    return asyncio.run(main_async(args.host, args.port))


if __name__ == "__main__":
    raise SystemExit(main())
