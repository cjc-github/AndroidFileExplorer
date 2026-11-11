"""Runtime configuration for AndroidFileExplorer backend."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _path_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    path = Path(raw)
    return path if path.is_absolute() else (BASE_DIR / path)


DATA_DIR = _path_env("DATA_DIR", BASE_DIR / "data")


@dataclass
class Settings:
    """All tunables live here; env overrides win."""

    host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    port: int = _int_env("SERVER_PORT", 8010)
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    adb_path: str = os.getenv("ADB_PATH", "adb")
    adb_server_host: str = os.getenv("ADB_SERVER_HOST", "127.0.0.1")
    adb_server_port: int = _int_env("ADB_SERVER_PORT", 5037)

    engine_connect_timeout: float = _float_env("ENGINE_CONNECT_TIMEOUT", 5.0)
    engine_command_timeout: float = _float_env("ENGINE_COMMAND_TIMEOUT", 30.0)
    remote_pool_size: int = _int_env("REMOTE_ENGINE_POOL_SIZE", 4)
    """Concurrent MDD command connections kept per remote machine."""

    device_offline_threshold: int = _int_env("DEVICE_OFFLINE_THRESHOLD", 2)
    metrics_interval_seconds: int = _int_env("METRICS_INTERVAL_SECONDS", 3)

    data_dir: Path = DATA_DIR
    upload_dir: Path = _path_env("UPLOAD_DIR", DATA_DIR / "uploads")
    download_dir: Path = _path_env("DOWNLOAD_DIR", DATA_DIR / "downloads")
    max_upload_bytes: int = _int_env("MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024)

    @property
    def database_url(self) -> str:
        override = os.getenv("DATABASE_URL")
        if override:
            return override
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.data_dir / 'afe.db'}"

    def ensure_dirs(self) -> None:
        """Create the writable runtime directories."""
        for directory in (self.data_dir, self.upload_dir, self.download_dir):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
