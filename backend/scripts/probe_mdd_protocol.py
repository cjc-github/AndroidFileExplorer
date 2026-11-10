"""Probe the raw MDD protocol traffic of a remote agent_server.

Prints exactly what comes back for the commands we rely on, so the parser
can be built against the real wire format instead of a guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.transports.mdd_client import MddClient

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.114.130"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 12345

COMMANDS = [
    "adb devices -l",
    "adb devices",
    "adb -s emulator-5554 shell ls -Al /sdcard",
    "adb -s emulator-5554 shell echo hello",
    "adb connect 127.0.0.1:5555",
]


def main() -> int:
    client = MddClient(HOST, PORT, connect_timeout=8, command_timeout=20)
    print(f"=== 连接 {HOST}:{PORT} ===")
    try:
        client.connect()
        print("connected")
    except Exception as exc:
        print(f"连接失败: {type(exc).__name__}: {exc}")
        return 1

    try:
        for command in COMMANDS:
            print()
            print("=" * 70)
            print(f"$ {command}")
            print("=" * 70)
            try:
                result = client.execute_command(command, timeout=20)
                print(f"success={result.success}")
                print(f"error  ={result.error!r}")
                print("---- raw message ----")
                print(repr(result.message))
            except Exception as exc:
                print(f"EXCEPTION {type(exc).__name__}: {exc}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
