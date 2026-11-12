"""Environment checks used by the startup log and the diagnostics endpoint.

The platform has a few system-level prerequisites that cannot be installed
with ``pip install``: the Android platform-tools (``adb``) and, for the SSH
direct mode, the Python package ``asyncssh``.  A missing prerequisite must
not take the whole backend down; it is reported with an actionable fix so
the UI can show what is wrong.
``"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.transports.local_adb import parse_adb_devices

#: Packages that the backend itself cannot run without.
BACKEND_IMPORTS = (
    "fastapi",
    "uvicorn",
    "sqlmodel",
    "aiosqlite",
    "dotenv",
    "loguru",
)


def _item(
    key: str,
    label: str,
    ok: bool,
    message: str,
    fix: str | None = None,
    required: bool = True,
) -> dict:
    return {
        "key": key,
        "label": label,
        "ok": ok,
        "required": required,
        "message": message,
        "fix": fix,
    }


def check_python() -> dict:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    return _item(
        "python",
        "Python 运行时",
        ok,
        f"Python {version}" if ok else f"Python {version}，需要 3.10 或更高版本",
        None if ok else "安装 Python 3.10+ 后重建 .venv",
    )


def check_backend_deps() -> dict:
    missing: list[str] = []
    for module in BACKEND_IMPORTS:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    ok = not missing
    return _item(
        "backend_deps",
        "后端 Python 依赖",
        ok,
        "依赖完整" if ok else f"缺少模块: {', '.join(missing)}",
        None if ok else "在项目根目录执行 .venv/bin/pip install -r backend/requirements.txt",
    )


def check_asyncssh() -> dict:
    try:
        module = importlib.import_module("asyncssh")
    except ImportError:
        return _item(
            "asyncssh",
            "SSH 直连依赖 (asyncssh)",
            False,
            "未安装 asyncssh；SSH 直连方式不可用",
            "执行 .venv/bin/pip install asyncssh（仅 SSH 直连需要）",
            required=False,
        )
    version = getattr(module, "__version__", "")
    return _item(
        "asyncssh",
        "SSH 直连依赖 (asyncssh)",
        True,
        f"asyncssh {version}".strip(),
        required=False,
    )


def check_adb(deep: bool = True) -> dict:
    configured = settings.adb_path
    resolved = shutil.which(configured)
    if resolved is None:
        return _item(
            "adb",
            "本机 adb",
            False,
            f"未找到 adb（ADB_PATH={configured}）；本机设备发现不可用",
            "安装 Android platform-tools 并加入 PATH，或在 backend/.env 配置 ADB_PATH",
            required=False,
        )

    env = dict(os.environ)
    env["ADB_SERVER_SOCKET"] = (
        f"tcp:{settings.adb_server_host}:{settings.adb_server_port}"
    )
    try:
        result = subprocess.run(
            [resolved, "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _item(
            "adb",
            "本机 adb",
            False,
            f"adb 无法执行: {exc}",
            "检查 ADB_PATH 是否指向可执行的 adb 二进制",
            required=False,
        )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return _item(
            "adb",
            "本机 adb",
            False,
            f"adb 版本检查失败: {message}",
            "重新安装 Android platform-tools",
            required=False,
        )

    version = (result.stdout or "").strip().splitlines()
    version_text = version[0] if version else "adb 可用"
    if not deep:
        return _item(
            "adb",
            "本机 adb",
            True,
            f"{version_text}（{resolved}）",
            required=False,
        )

    try:
        devices = subprocess.run(
            [resolved, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _item(
            "adb",
            "本机 adb",
            False,
            f"adb server 检查失败: {exc}",
            "确认 ADB_SERVER_HOST / ADB_SERVER_PORT 指向可用的 adb server",
            required=False,
        )
    if devices.returncode != 0:
        message = (devices.stderr or devices.stdout or "").strip()
        return _item(
            "adb",
            "本机 adb",
            False,
            f"adb server 不可达: {message}",
            "启动 adb server 或检查 ADB_SERVER_HOST / ADB_SERVER_PORT",
            required=False,
        )

    attached = len(parse_adb_devices(devices.stdout or ""))
    return _item(
        "adb",
        "本机 adb",
        True,
        f"{version_text}（{resolved}），adb server 正常，当前 {attached} 台设备",
        required=False,
    )


def check_storage() -> dict:
    try:
        settings.ensure_dirs()
        for directory in (settings.data_dir, settings.upload_dir, settings.download_dir):
            with tempfile.NamedTemporaryFile(dir=directory, delete=True):
                pass
    except OSError as exc:
        return _item(
            "storage",
            "数据目录写权限",
            False,
            f"数据目录不可写: {exc}",
            "检查 DATA_DIR / UPLOAD_DIR / DOWNLOAD_DIR 的权限和挂载状态",
        )
    return _item(
        "storage",
        "数据目录写权限",
        True,
        f"可写：{settings.data_dir}",
    )


def run_checks(deep: bool = True) -> list[dict]:
    """Return one diagnostic item per environment prerequisite."""
    return [
        check_python(),
        check_backend_deps(),
        check_asyncssh(),
        check_adb(deep=deep),
        check_storage(),
    ]


def summarize(checks: list[dict]) -> dict:
    """Overall status: every required check must pass."""
    return {
        "ok": all(item["ok"] for item in checks if item["required"]),
        "checks": checks,
    }
