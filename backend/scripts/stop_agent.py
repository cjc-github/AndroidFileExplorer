"""Stop a running device agent on a remote machine.

The agent only accepts adb commands, so it cannot stop itself.  This helper
uses the MDD channel of the (still running) agent to kill the process.

Usage::

    cd backend && ../.venv/bin/python scripts/stop_agent.py \\
        --host 192.168.114.130 --port 12345 --pattern afe-agent/agent.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.transports.mdd_client import MddClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop the device agent")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=12345)
    parser.add_argument("--pattern", default="afe-agent/agent.py")
    args = parser.parse_args()

    client = MddClient(args.host, args.port, connect_timeout=10, command_timeout=30)
    try:
        client.connect()
    except Exception as exc:
        print(f"[warn] 无法连接 {args.host}:{args.port}: {exc}")
        return 0

    try:
        # The pid file is the reliable way to stop the agent we deployed.
        for command in (
            "cat ~/afe-agent/agent.pid 2>/dev/null | xargs -r kill 2>/dev/null; echo KILLED_BY_PID",
            # The agent is started with a relative path, so match on the file
            # name as well.
            "pkill -f 'agent.py --host' 2>/dev/null; echo KILLED_BY_PATTERN",
            "sleep 2",
            "ss -tlnp 2>/dev/null | grep -E '1807[0-9]' || echo PORT_FREE",
        ):
            result = client.execute_command(command, timeout=30)
            text = (result.message or "").strip()
            print(f">>> {command[:50]:52s} ok={result.success} {text[:80]}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
