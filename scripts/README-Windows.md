# Windows 启动脚本使用说明

对应 `scripts/start.sh` / `dev.sh` / `check-env.sh` 的 Windows 原生版本，全部放在 `scripts/` 下，**不改动任何现有 Unix 脚本或工程源码**。

## 快速开始

| 操作 | 命令 / 双击 | 说明 |
| --- | --- | --- |
| 首次安装 | 双击 `scripts\setup.cmd` | 创建 `.venv`、装后端依赖、`npm ci`、跑环境自检 |
| 后台启动 | 双击 `scripts\start.cmd` | 后端 `:8010` + 前端 `:10012`，日志在 `logs\`，窗口可关 |
| 查看状态 | `scripts\start.cmd status` | 显示 PID、端口监听、访问地址 |
| 停止服务 | 双击 `scripts\stop.cmd` | 按 PID + 子进程树结束，并兜底清理残留 |
| 重启 | `scripts\start.cmd restart` | 先停后起 |
| 前台开发 | 双击 `scripts\dev.cmd` | Ctrl+C 同时停掉前后端，日志直接可见 |
| 仅起后端 | `scripts\dev.cmd backend` | 跳过 Node 检查 |
| 仅起前端 | `scripts\dev.cmd frontend` | — |
| 跟踪日志 | `scripts\start.cmd -Tail` | 启动后持续跟踪日志，Ctrl+C 只退出跟踪、不停服务 |

PowerShell 直接调用（无需 `.cmd`）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 stop
powershell -ExecutionPolicy Bypass -File .\scripts\check-env.ps1 -BackendOnly
```

## 访问地址

| 服务 | 地址 |
| --- | --- |
| 前端界面 | http://127.0.0.1:10012 |
| 后端 API | http://127.0.0.1:8010/api/v1 |
| API 文档 | http://127.0.0.1:8010/docs |

绑定 `0.0.0.0`，局域网内其他机器可用本机 IP 访问（记得放行 Windows 防火墙）。

## 与 Unix 脚本的对应关系

| Unix | Windows | 关键差异 |
| --- | --- | --- |
| `.venv/bin/python` | `.venv\Scripts\python.exe` | venv 目录名不同 |
| `nohup ... &` + `$!` | `Start-Process -PassThru` + `.pid` 文件 | Windows 无 `nohup` / `$!` |
| `pkill -f` | `taskkill /PID /T /F` + `Get-CimInstance` 命令行匹配 | 按 PID 精确结束，兜底按命令行过滤本工程 |
| `/dev/tcp` 探端口 | `[System.Net.Sockets.TcpClient]::BeginConnect` | 400ms 超时，不挂起 |
| `source nvm.sh && nvm use 22` | 直接用 PATH 里的 `node` / `npm.cmd` | Windows 无 nvm；要求 Node 20+ |
| `timeout 5 adb version` | `Start-Process` + `WaitForExit(5000)` | 避免 adb 挂死 |
| `tail -30` | `Get-Content -Tail 30` | — |

## 环境变量覆盖

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SERVER_PORT` | `8010` | 后端端口 |
| `FRONTEND_PORT` | `10012` | 前端端口 |
| `BIND_HOST` | `0.0.0.0` | 绑定地址 |
| `VENV_PY` | `<root>\.venv\Scripts\python.exe` | 自定义 Python 路径 |
| `ADB_PATH` | `adb` | 本机 adb 可执行文件 |

> ⚠️ **端口一致性**：`frontend\vite.config.ts` 里的 `/api` 代理固定指向 `http://127.0.0.1:8010`。
> 若修改了 `SERVER_PORT`，必须同步修改该代理目标，否则前端请求会打到空端口。

## 环境前置

| 组件 | 要求 | 缺失表现 |
| --- | --- | --- |
| Python | 3.10+ | `setup.cmd` 会创建 `.venv` 并安装依赖 |
| Node.js | 20+ | 前端无法启动；`check-env.ps1` 报 `[fail]` |
| adb | 可选 | 仅影响本机设备发现，远程机器仍可用；报 `[warn]` |
| asyncssh | 可选 | SSH 直连不可用，agent 接入不受影响；报 `[warn]` |

## 日志与 PID

```
logs\
├── backend.log          # 后端 stdout
├── backend.err.log      # 后端 stderr（启动失败时先看这里）
├── backend.pid
├── frontend.log
├── frontend.err.log
└── frontend.pid
```

启动脚本会等待两个端口真正监听后才返回；失败时自动打印 `.err.log` 尾部 30 行。

## 执行策略说明

Windows 默认可能禁止运行未签名的 `.ps1`。所有 `.cmd` 包装都带
`-ExecutionPolicy Bypass`，**只作用于该次调用**，不修改系统策略。
双击 `.cmd` 即可，无需手动 `Set-ExecutionPolicy`。

所有 `.ps1` 均保存为 **UTF-8 with BOM**：Windows PowerShell 5.1 会把无 BOM 的
UTF-8 文件按系统 ANSI/GBK 解码，导致中文字符串里的引号被乱码字节打断、脚本
直接解析失败。用 `powershell.exe -File` 直接调用时也必须保留 BOM，不要用会
剥掉 BOM 的编辑器重存。

## 预览稿入口

`ui-preview\打开预览.cmd` 双击即可在默认浏览器打开静态 UI 提案稿
（`optimized-ui.html`），不依赖 Node / Python / 后端服务。
