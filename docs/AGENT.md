# 设备侧 Agent 使用指南

agent（`agent/agent.py`）是跑在**远程机器**上的 adb 桥：平台把 `adb ...` 命令通过
TCP 发给它，它在本地执行后把结果回传。本指南回答：什么时候需要它、怎么装、
怎么配密钥、怎么排障。

---

## 1. 什么时候需要

| 场景 | 需要跑 agent 吗 |
|---|---|
| Android 设备插在**平台本机** | ❌ 不需要（平台直接调本机 adb） |
| Android 设备插在**另一台机器**（实验室电脑、测试服务器） | ✅ 那台机器上跑一个 |

原因：adb server 只能操作插在它所在机器上的设备，平台跨不过网络，
agent 就是那台机器上的"代执行者"。

## 2. 前置条件

远程机器上只需要两样东西，**不需要克隆本项目**：

- `python3`（≥3.10，仅标准库，无 pip 依赖）
- `adb`（在 PATH 里，或部署时可通过参数指定路径）

网络要求：**平台后端 → 远程机器 agent 端口** TCP 可达（默认 `18077`）。

部署前无需手动检查：`install-agent.sh` / `deploy_agent.py` 会先通过旧通道执行
`command -v python3`、Python 版本检查、`command -v adb`、`adb version` 和
`adb devices -l`；缺失时直接打印安装提示并停止传输。平台侧也可以运行
`./scripts/check-env.sh --backend-only` 检查本地依赖。

## 3. 快速接入（推荐）

在**本项目目录**（平台所在机器）执行一条命令：

```bash
./scripts/install-agent.sh <远程机器IP> [旧agent端口] [新agent监听端口]

# 例：机器 192.168.114.130 上已有一个旧 agent 在 12345，
# 部署本项目 agent 到 18077
./scripts/install-agent.sh 192.168.114.130 12345 18077
```

脚本自动完成三步：

1. 停掉远程机器上的旧版 agent（如果有）；
2. 通过旧通道把 `agent.py` 复制到 `~/afe-agent/` 并后台启动；
3. 在平台登记这台机器并立即探测。

> 首次接入全新机器（上面没有旧 agent）时，脚本无法远程部署——
> 先手动方式（下一节）启动一个，或用 ssh 直接复制。

登记成功后，打开平台「远程机器连接配置」即可看到该机器与它的设备数。

## 4. 手动接入

适合没有旧 agent 可用的全新机器：

```bash
# 1. 把单个文件复制过去（agent 只有一个文件）
scp agent/agent.py user@192.168.114.130:~/afe-agent/agent.py

# 2. 在远程机器上启动（推荐带共享密钥，见第 5 节）
ssh user@192.168.114.130
mkdir -p ~/afe-agent && cd ~/afe-agent
nohup python3 agent.py --host 0.0.0.0 --port 18077 > agent.log 2>&1 &

# 3. 回到平台机器，登记并验证
./scripts/connect-remote-machine.sh 192.168.114.130 18077
```

### 生产环境建议用 systemd 常驻

在远程机器上创建 `/etc/systemd/system/afe-agent.service`：

```ini
[Unit]
Description=AndroidFileExplorer device agent
After=network.target

[Service]
User=<你的用户>
ExecStart=/usr/bin/python3 /home/<你的用户>/afe-agent/agent.py --host 0.0.0.0 --port 18077 --token <你的密钥>
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now afe-agent
```

## 5. 共享密钥（推荐启用）

不设密钥时，任何能连到 agent 端口的进程都能执行 adb 命令。局域网环境也建议启用：

```bash
# agent 侧（部署时）
python3 agent.py --host 0.0.0.0 --port 18077 --token <你的密钥>

# 平台侧：登记时填同一个密钥（界面「远程机器连接配置」→ 新增机器 → 共享密钥）
# 或调用 API：POST /api/v1/engines {"token": "<你的密钥>", ...}
```

握手方式：客户端连接后必须先发 `AUTH <token>`，错误密钥一条命令都执行不了。

> **注意**：`install-agent.sh` 登记时**不会**自动带密钥。用 `--token` 部署后，
> 请在平台界面为该机器补填共享密钥，或删除后用 API 带 token 重新登记。

## 6. 日常运维

```bash
# 停止远程 agent（通过 SHUTDOWN 命令，不需要 ssh）
.venv/bin/python backend/scripts/stop_agent.py --host 192.168.114.130 --port 12345

# 通过平台 API 现场探测一台机器
curl -X POST http://127.0.0.1:8010/api/v1/engines/<id>/probe

# 重新部署/升级 agent（复用已有通道）
.venv/bin/python backend/scripts/deploy_agent.py --host <IP> --port <旧端口> \
  --remote-port 18077 --start --token <你的密钥>
```

agent 命令行参数速查：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `12345` | 监听端口 |
| `--token` | （无） | 共享密钥，也可用环境变量 `AFE_AGENT_TOKEN` |
| `--adb` | PATH 里的 adb | adb 二进制路径 |
| `--adb-server` | 本机默认 | 远端 adb server，形如 `host:port` |
| `--max-concurrent` | 8 | 同时运行的 adb 进程上限（`AFE_ADB_MAX_CONCURRENT`） |

## 7. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 登记后 `status=error`，提示连接失败 | agent 没跑或端口不通：`bash -c "</dev/tcp/IP/端口"` 试连通性；防火墙放行 |
| `agent 认证失败: ERROR: authentication failed` | 平台记录里的密钥与 agent `--token` 不一致，改一侧即可 |
| 机器在线但设备数为 0 | 远程机器的 adb server 没起或没插设备：在那台机器上跑 `adb devices` 验证 |
| 设备显示 `unauthorized` | 在**设备屏幕上**确认 USB 调试授权弹窗 |
| 设备显示 `offline` | 换 USB 口 / `adb kill-server` 后重连；平台会自动恢复 |
| 平台日志出现 `temporarily unavailable` | 该机器刚失联，平台短跳过（几秒）；恢复后自动探测回来 |

## 8. 协议速查

agent 的完整协议与安全模型见 [README「设备侧 agent」](../README.md#设备侧-agent)。
一行一个请求，答案以 `===END===` 结尾：

```
adb devices -l          → 命令信封（标准输出 / 错误信息）
FILE_PUSH:{json}\n+字节流 → adb push 到设备 → OK
FILE_PULL:{json}\n      → FILE_INFO/FILE_DATA 头 + 字节流（分块回传）
PING                    → PONG
SHUTDOWN                → agent 自行退出
```
