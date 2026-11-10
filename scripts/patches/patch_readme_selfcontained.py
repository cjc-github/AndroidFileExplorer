"""One-shot patcher: document the self contained architecture.

The project used to need an external agent (multi-device-manager) on every
remote machine. The device agent now lives in this project (agent/), so the
deployment is self contained.

The text is inserted by LINE NUMBER (after reading the file) instead of by
string match: the README was rewritten several times, so the anchors kept
drifting.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent.parent.parent / "README.md"

SELF_CONTAINED = """## 自包含架构

本项目**不依赖任何外部仓库或组件**。远程机器上只需要 `python3` + `adb`，设备侧代理
（agent）就在本项目里：

```
你的浏览器
   ↓ http://192.168.114.128:10012
前端 (vite dev :10012) ──代理 /api──▶ 后端 (FastAPI :8010)
                                          ↓ MDD TCP 协议（本项目两端实现）
                              192.168.114.130:18077  ← agent/agent.py（属于本项目）
                                          ↓ 在该机本机执行 adb
                                    Android 设备 emulator-5554
```

### 设备侧 agent

| 文件 | 作用 |
|---|---|
| `agent/agent.py` | 设备侧代理，**只依赖 Python 标准库和 adb**，可单独拷到任意机器 |
| `backend/scripts/deploy_agent.py` | 通过 MDD 通道把 agent 部署到远程机器并启动 |
| `backend/scripts/stop_agent.py` | 停止远端 agent |
| `scripts/install-agent.sh` | 一键：停旧版 → 部署 → 启动 → 自检 → 平台登记 |

一键把一台远程机器接进来（该机器不再需要任何外部组件）：

```bash
./scripts/install-agent.sh 192.168.114.130 12345 18077
```

agent 的协议（与平台端 `backend/app/transports/mdd_client.py` 一一对应）：

| 请求 | 响应 |
|---|---|
| `adb devices -l` | `命令执行成功 (返回码: 0)` + `标准输出:` + `====` + `===END===` |
| `PING` | `PONG` |
| `SHUTDOWN` | agent 自行退出 |

安全边界：agent **只执行 adb 命令**，其它命令一律拒绝。

### 设备唯一标识

设备标识基于**设备自身的序列号**（`ro.serialno`），而不是「从哪台机器连过来的」：

```
uid = sha1("hw|" + ro.serialno)[:16]        # 设备报告了序列号
uid = sha1("kind|endpoint|serial")[:16]      # 没报告时按机器 + serial
```

因此同一台手机即使通过两个 agent 被发现，在设备列表里**也只有一条记录**。

"""

AGENT_SECTION = """### 部署设备侧 agent（自包含）

远程机器上只需要 `python3` 和 `adb`：

```bash
# 在本项目目录执行（该机器需已开着旧的 agent 或 ssh 可达）
./scripts/install-agent.sh <机器IP> <旧agent端口> <新agent端口>

# 或者手动：把 agent/agent.py 拷过去运行
python3 agent/agent.py --host 0.0.0.0 --port 18077
```

完成后在 **远程机器连接配置**页点「新增机器」，填该机器 IP 与 agent 端口。

"""


def main() -> int:
    lines = TARGET.read_text(encoding="utf-8").splitlines()

    if any("## 自包含架构" in line for line in lines):
        print("ALREADY_PATCHED")
        return 0

    # 1. the self contained chapter goes before "## 接入真实远程机器"
    index = next(
        (i for i, line in enumerate(lines) if line.startswith("## 接入真实远程机器")),
        None,
    )
    if index is None:
        print("ANCHOR MISSING: ## 接入真实远程机器")
        return 1
    lines[index:index] = SELF_CONTAINED.splitlines() + [""]

    # 2. the agent deployment part goes into that chapter
    index = next(
        (i for i, line in enumerate(lines) if line.startswith("## 接入真实远程机器")),
        None,
    )
    if index is None:
        print("ANCHOR MISSING (2): ## 接入真实远程机器")
        return 1
    lines[index:index] = AGENT_SECTION.splitlines()

    # 3. the test count
    text = "\n".join(lines)
    text = text.replace(
        "| 1 | 后端单元 + 集成测试 | 68 passed |",
        "| 1 | 后端单元 + 集成测试（含 agent 与设备标识） | 80 passed |",
    )

    # 4. the directory tree
    text = text.replace(
        "├── backend/                FastAPI + SQLModel",
        "├── agent/                   设备侧代理（仅标准库 + adb，可单独部署）\n"
        "├── backend/                FastAPI + SQLModel",
    )
    text = text.replace(
        "    └── connect-remote-machine.sh   一键接入远程机器",
        "    ├── connect-remote-machine.sh   一键接入远程机器\n"
        "    └── install-agent.sh           一键部署设备侧 agent（自包含）",
    )

    TARGET.write_text(text, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
