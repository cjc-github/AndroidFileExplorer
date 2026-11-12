"""Deploy the device agent to a remote machine over the MDD protocol.

The platform can already run commands on a remote machine through the MDD
agent; this script uses that channel to copy the agent there and start it.
It is the bootstrap for a self contained deployment: once the agent runs on
the remote machine, the platform no longer needs any other component there.

Usage::

    cd backend && ../.venv/bin/python scripts/deploy_agent.py \\
        --host 192.168.114.130 --port 12345 \\
        --agent ../agent/agent.py --remote-dir ~/afe-agent --remote-port 18077
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.transports.mdd_client import MddClient  # noqa: E402

CHUNK_SIZE = 40_000


def run(client: MddClient, command: str, timeout: int = 30) -> tuple[bool, str]:
    result = client.execute_command(command, timeout=timeout)
    return result.success, (result.message or "").strip()


def preflight(client: MddClient) -> bool:
    """Check remote python3 / adb BEFORE transferring the agent."""
    print("[1/5] 远端环境检查 ...")
    checks = (
        (
            "python3",
            "command -v python3",
            "远端缺少 python3",
            "在远端安装 Python 3.10+（例如 apt install python3）",
            False,
        ),
        (
            "Python 版本",
            'python3 -c "import sys; print(sys.version_info >= (3, 10))"',
            "远端 Python 版本低于 3.10",
            "升级远端 Python 到 3.10 或更高版本",
            True,
        ),
        (
            "adb",
            "command -v adb",
            "远端缺少 adb",
            "在远端安装 Android platform-tools，并确保 adb 在 PATH 中",
            False,
        ),
        (
            "adb version",
            "adb version",
            "远端 adb 无法执行",
            "重新安装 Android platform-tools",
            False,
        ),
        (
            "adb devices",
            "adb devices -l",
            "远端 adb devices 执行失败",
            "检查远端 adb server 是否可用",
            False,
        ),
    )

    for label, command, missing, hint, expect_true in checks:
        ok, output = run(client, command, timeout=30)
        first = output.splitlines()[0] if output else ""
        print(f"      {label}: {'OK' if ok else 'FAIL'} {first[:120]}")
        if not ok or (expect_true and "True" not in output):
            print(f"[error] {missing}", file=sys.stderr)
            print(f"        修复: {hint}", file=sys.stderr)
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy the device agent")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=12345)
    parser.add_argument(
        "--agent",
        default=str(BACKEND_ROOT.parent / "agent" / "agent.py"),
        help="本地 agent 文件",
    )
    parser.add_argument("--remote-dir", default="~/afe-agent")
    parser.add_argument("--remote-port", type=int, default=18077)
    parser.add_argument("--remote-host", default="0.0.0.0")
    parser.add_argument(
        "--token",
        default=None,
        help="为远端 agent 配置共享密钥（启用认证）",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="传输完成后在远端启动 agent",
    )
    args = parser.parse_args()

    agent_path = Path(args.agent).expanduser()
    if not agent_path.is_file():
        print(f"[error] agent 文件不存在: {agent_path}", file=sys.stderr)
        return 1

    payload = base64.b64encode(agent_path.read_bytes()).decode()
    chunks = [payload[i : i + CHUNK_SIZE] for i in range(0, len(payload), CHUNK_SIZE)]
    print(f"[2/5] agent 文件 {agent_path} -> base64 {len(payload)} 字节, {len(chunks)} 块")

    client = MddClient(args.host, args.port, connect_timeout=10, command_timeout=60)
    try:
        client.connect()
    except Exception as exc:
        print(f"[error] 无法连接 {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1

    try:
        if not preflight(client):
            return 1

        ok, out = run(
            client,
            f"mkdir -p {shlex.quote(args.remote_dir)} && rm -f ~/afe-agent.b64",
        )
        if not ok:
            print(f"[error] 创建目录失败: {out}", file=sys.stderr)
            return 1

        print("[3/5] 传输中 ...")
        for index, chunk in enumerate(chunks, start=1):
            ok, out = run(
                client,
                f"printf %s {shlex.quote(chunk)} >> ~/afe-agent.b64",
                timeout=60,
            )
            if not ok:
                print(f"[error] 第 {index} 块传输失败: {out}", file=sys.stderr)
                return 1

        print("[4/5] 解码并校验 ...")
        target = f"{args.remote_dir}/agent.py"
        ok, out = run(
            client,
            f"base64 -d ~/afe-agent.b64 > {shlex.quote(target)} && "
            f"chmod +x {shlex.quote(target)} && "
            f"python3 -c 'import ast,sys;ast.parse(open(sys.argv[1]).read())' {shlex.quote(target)} "
            f"&& echo DECODE_OK && wc -l {shlex.quote(target)}",
            timeout=60,
        )
        print(f"      {out}")
        if not ok or "DECODE_OK" not in out:
            print("[error] 远端文件校验失败", file=sys.stderr)
            return 1

        if not args.start:
            print("[5/5] 已传输（未启动）。加 --start 可直接启动。")
            return 0

        print("[5/5] 启动 agent ...")
        log = f"{args.remote_dir}/agent.log"
        pid_file = f"{args.remote_dir}/agent.pid"
        # `$HOME` instead of `~`: the tilde is not expanded inside the
        # redirection of a background job, which leaves the log file missing.
        home = run(client, "echo $HOME", timeout=15)[1].strip() or "/root"
        remote_home = home.rstrip("/")
        log = f"{remote_home}/{args.remote_dir.lstrip('~/')}/agent.log"
        pid_file = f"{remote_home}/{args.remote_dir.lstrip('~/')}/agent.pid"
        target = f"{remote_home}/{args.remote_dir.lstrip('~/')}/agent.py"

        # An absolute path keeps the command line greppable when the agent
        # has to be stopped again.
        token_arg = f" --token {shlex.quote(args.token)}" if args.token else ""
        ok, out = run(
            client,
            f"cd {shlex.quote(remote_home)} && "
            f"nohup python3 {shlex.quote(target)} "
            f"--host {shlex.quote(args.remote_host)} "
            f"--port {args.remote_port}{token_arg} > {shlex.quote(log)} 2>&1 & "
            f"echo $! > {shlex.quote(pid_file)} && sleep 2 && "
            f"cat {shlex.quote(pid_file)} && tail -3 {shlex.quote(log)}",
            timeout=60,
        )
        print(f"      {out}")
        if not ok:
            print("[error] agent 启动失败", file=sys.stderr)
            return 1

        # Verify the new agent really answers.
        time.sleep(1)
        probe = MddClient(args.host, args.remote_port, connect_timeout=8, command_timeout=20)
        try:
            probe.connect()
            result = probe.execute_command("adb devices", timeout=20)
            print(f"      自检: adb devices -> {result.success} {(result.message or '').strip()[:120]}")
            if not result.success:
                print("[error] 新 agent 自检失败", file=sys.stderr)
                return 1
        finally:
            probe.close()
        print(f"[ok] agent 已部署并运行在 {args.host}:{args.remote_port}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
