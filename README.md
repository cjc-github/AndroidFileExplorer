# AndroidFileExplorer

Android 设备文件资源 / 文件管理能力：**远程机器连接配置**、**设备资源管理**、**文件浏览**
三个独立入口，加上设备发现、目录结构浏览、拉取上传、运行状态监控。

本工程参考 `/home/test/tcl/frontend-api` + `/home/test/tcl/platform-api` 中已有的设备模块，
但只保留**核心能力**（不含登录注册、测试目标、函数分析、运行调度等）。

## 访问地址

左侧竖排菜单四个入口（三个业务界面 + 平台设置）：

| 界面 | 地址 | 内容 |
|---|---|---|
| **远程机器连接配置** | http://192.168.114.128:10012/machines | 机器列表 / 新增 / 测试连接 / 删除 |
| **设备资源管理** | http://192.168.114.128:10012/devices | 设备表 / 按设置轮询 / 连接断开 / 浏览文件 |
| **文件浏览**（选设备） | http://192.168.114.128:10012/files | 列出所有设备，点「浏览文件」进入 |
| **文件浏览**（某设备） | http://192.168.114.128:10012/devices/{uid}/files | 目录 / 搜索 / 上传 / 拉取 |
| **平台设置** | http://192.168.114.128:10012/settings | 刷新间隔 / 自动同步 / 自动 root / 环境自检 |
| 后端 API | http://192.168.114.128:8010/api/v1 | |
| API 文档 | http://192.168.114.128:8010/docs | |

两个服务都绑定 `0.0.0.0`，可从局域网内任意机器访问。

```bash
./scripts/start.sh          # 后台启动两个服务，日志在 ./logs/
./scripts/start.sh stop     # 停止
./scripts/dev.sh            # 前台启动（Ctrl+C 退出）
```

## 三个界面 + 平台设置

### 1. 远程机器连接配置 `/machines`

管理挂载 ADB server 的机器：本机 + 远程 `agent_server`。

| 功能 | 说明 |
|---|---|
| 机器列表 | 名称、地址、连接状态、设备数、最后探测时间、错误信息 |
| 新增机器 | 登记后立即探测，失败会显示原因但**不回滚登记**；同一台机器重复登记返回 400 |
| 编辑机器 | 可改名、改地址/端口、轮换共享密钥或 SSH 密码、修改 adb 路径，**保留设备记录**；密钥/密码留空表示不修改 |
| 共享密钥 | 登记时可为该机器填 agent 密钥（`AUTH <token>` 握手） |
| SSH 认证状态 | SSH 机器会显示「已保存 SSH 密码 / 使用本机 SSH 密钥」 |
| 测试连接 | 现场探测该机器，返回探测到的设备数 |
| 删除 | 删除机器及其设备记录，同时清理连接缓存；**本机不可删除** |

一键接入远程机器：

```bash
./scripts/connect-remote-machine.sh 192.168.114.130 12345
```

### 2. 设备资源管理 `/devices`

| 功能 | 说明 |
|---|---|
| 设备表 | **多选批量操作**（批量尝试/取消 root，逐台反馈失败原因）、设备名（**可点击改名**）、**品牌**、序列号、**唯一标识 UID**、状态、**Root**（是/否）、**系统**（Android 版本 + API level + CPU 架构）、**CPU**（圆环 + 核心数 + 当前/最高频率）、**内存**（圆环 + 用量/总量）、**存储**（`/data`、`/sdcard` 用量条）、最后在线、错误信息 |
| **设备详情** | 行内「详情」抽屉：状态、序列号、UID、传输方式、软硬件属性、CPU/内存/存储、root、最后在线与错误信息 |
| **分页** | 服务端分页（默认 20 条/页，可切 10/20/50/100），关键字过滤覆盖全量设备，仅对当前页设备采集指标 |
| **过滤 / 排序** | 按名称 / 型号 / 序列号 / UID / 品牌过滤（服务端全量）；设备名、最后在线为服务端排序（跨页有效）；CPU / 内存展示当前页数据 |
| 自动发现 | 首次加载自动同步；之后轻量轮询，并每 30 秒做一次恢复性全量同步（设备重新插入后无需手动「同步」） |
| 轮询 | 按平台设置刷新运行状态（CPU / 内存 / load average），默认 3 秒；后台轮询不遮挡表格 |
| 生命周期 | 「连接」「断开」按唯一标识操作；行级按钮只显示当前设备的 loading |
| 进入文件浏览 | 设备行内「浏览文件」按钮（第一列位置，主色描边） |

#### 性能

设备列表接口**同时返回指标**，不需要前端再逐台去拉：

| 接口 | 耗时 |
|---|---|
| `GET /api/v1/devices` | 0.02s |
| `GET /api/v1/devices?refresh=true` | 0.01s |
| `GET /api/v1/devices?page=1&page_size=20` | 0.02s（只采样当前页）|
| `GET /api/v1/devices/{uid}/metrics` | 0.02s |

做到这一点靠三件事：

1. **多机器并发同步** —— 机器之间互不依赖，串行会让请求等最慢的那台。
2. **连接复用** —— 远程机器的 TCP 连接常驻，不再每次重新握手。
3. **指标缓存（2.5s）** —— 轮询周期内不重复打设备；品牌只读一次。

不可达的机器会被标记为「暂时不可用」并短期跳过，不再每次都等满超时。

### 3. 文件浏览 `/files` → `/devices/{uid}/files`

从左侧菜单「文件浏览」进入，**先选设备**再浏览：

| 功能 | 说明 |
|---|---|
| 设备选择 | 服务端分页（默认 20 条/页），可按名称 / 型号 / 品牌 / 序列号 / UID 过滤（覆盖全量设备）；统一优先显示设备别名 |
| **路径直达** | 路径框输入任意路径直接跳转，例如 `/data/local/tmp` |
| **面包屑逐级回退** | `根目录 / data / local / tmp`，每一级都可点击跳转 |
| 目录结构 | 权限 / 所属 / 修改时间 / 大小；名称 / 时间 / 大小可排序；显示条目数量 |
| 关键字搜索 | 可选择「当前目录 / 全设备」范围递归搜索；受保护目录自动跳过；并发列举 + 跳过 `/proc` `/sys` 等伪文件系统；结果截断时可「加载更多」（200 → 1000） |
| **新建目录** | 在当前目录下创建目录（`mkdir -p`） |
| **重命名 / 移动** | 行内操作，可改名，也可填写目标目录实现移动 |
| **复制** | 行内「更多」→「复制」，把文件/目录复制到指定路径 |
| **修改权限** | 行内「更多」→「修改权限」，支持 chmod（如 644）与 chown（所有者/用户组） |
| **预览** | 文本与图片文件可直接预览（上限 2MB），二进制文件提示下载 |
| **删除** | 行内操作，删除目录时递归删除，操作前二次确认 |
| **批量删除** | 多选后一次删除；限并发执行，逐项反馈成功 / 失败原因，失败项保留选中 |
| **批量下载** | 多选文件 / 目录后打包为 zip 下载，显示进度并可取消 |
| 上传 | 支持多文件顺序上传到当前目录；显示单文件进度、可取消；同名文件覆盖前确认；大文件不受 60 秒前端超时限制 |
| 拉取 | 从设备拉取到服务端落地目录 |
| **下载到浏览器** | 「下载」按钮在页面显示进度并可取消；失败原因在页面提示；超过 100MB 自动降级为浏览器直接下载 |
| **目录打包下载** | 「打包目录」或行内「更多」→「打包下载」把目录打成 zip（服务端限制 200 个文件 / 200MB） |
| **符号链接** | 链接行可尝试进入；显示链接目标；非目录链接会给出错误提示 |
| **大目录虚拟滚动** | 数千条目只渲染可视区，滚动不卡 |
| **路径写进 URL** | 当前路径与搜索词同步到地址栏，可分享 / 浏览器前进后退 |
| 切换设备 | 文件页顶部「切换设备」返回选择页 |
| **root 提权** | 未 root 时显示「尝试 root」；已 root 时显示「取消 root」，两者可来回切换 |

#### 为什么需要路径直达

Android 上 **父目录不可读、子目录可读** 是常态（未 root 的设备）：

| 路径 | shell 用户 |
|---|---|
| `/data` | ❌ Permission denied |
| `/data/local` | ❌ Permission denied |
| `/data/local/tmp` | ✅ 可读 |

如果只能靠逐级点击，用户在 `/` 点 `data` 就被 403 挡住，**永远到不了 `/data/local/tmp`**。
所以路径框是必需品，不是锦上添花。

#### 越权时的表现

访问受保护目录时，页面顶部显示明确的告警，而不是空白页：

```
权限不足，无法访问该目录
权限不足：无法访问 emulator-5554（ls: /data/: Permission denied）。
该路径需要 root 权限，请使用已 root 的设备或以 su 身份访问。
该路径需要 root 权限。可以直接在上方路径框输入更深层的可访问路径
（例如 /data/local/tmp），或改用已 root 的设备。
```

路径级权限不足**不会**改写设备生命周期状态（设备仍是 `online`）。

### 4. 平台设置 `/settings`

| 设置项 | 取值 | 作用 |
|---|---|---|
| 设备列表刷新间隔 | 1–60 秒（默认 3） | 设备页、文件选择页、文件浏览页共用的状态刷新周期 |
| 后台自动同步间隔 | 0–86400 秒（默认 0） | 0 = 后台不再周期探测（页面打开期间仍会做恢复性同步，也可手动「同步」）；>0 则后台周期发现设备 |
| 自动尝试 root | 开关（默认关） | 每次同步后对未 root 的在线设备执行 `adb root`（每个同步周期只执行一次） |
| 环境自检 | 只读 | 检查 Python / 后端依赖 / asyncssh / adb / 数据目录；缺失项给出修复命令 |

设置保存即生效，**无需重启**。

## 自包含架构

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

> 📖 **完整使用指南见 [docs/AGENT.md](docs/AGENT.md)**：
> 前置条件、手动部署、systemd 常驻、共享密钥配置、日常运维与故障排查。

```bash
./scripts/install-agent.sh 192.168.114.130 12345 18077

# 启用认证（推荐）：agent 与平台两侧配置同一个密钥
python3 agent/agent.py --host 0.0.0.0 --port 18077 --token <你的密钥>
```

agent 的协议（与平台端 `backend/app/transports/mdd_client.py` 一一对应）：

| 请求 | 响应 |
|---|---|
| `adb devices -l` | `命令执行成功 (返回码: 0)` + `标准输出:` + `====` + `===END===` |
| `FILE_PUSH:{json}\n` + 字节流 | agent 收满 `file_size`（边收边算 md5，不符拒收）→ `adb push` → `OK` |
| `FILE_PULL:{json}\n` | `FILE_INFO:FILE:<名>` + `FILE_DATA:<名>:<size>:<md5>` + 分块字节流（1MB/块）→ 客户端校验 md5 后回 `OK` |
| `PING` | `PONG` |
| `SHUTDOWN` | agent 自行退出 |
| `AUTH <token>` | 连接后的第一行：共享密钥认证（配置了密钥时必须先通过） |

安全边界：

- agent **只执行 adb 命令**，其它命令一律拒绝；
- 启动时加 `--token <密钥>`（或环境变量 `AFE_AGENT_TOKEN`）即启用认证，未认证的连接在第一条命令前就会被断开；
- 平台侧在 **远程机器连接配置** 里填写同一密钥（存每台机器自己的记录里），部署时用 `--token` 传入；
- adb 进程并发上限 `--max-concurrent`（默认 8，或环境变量 `AFE_ADB_MAX_CONCURRENT`）：多设备命令可并行，突发流量不会压垮宿主机。

### 设备唯一标识

设备标识基于**设备自身的序列号**（`ro.serialno`），而不是「从哪台机器连过来的」：

```
uid = sha1("hw|" + ro.serialno)[:16]        # 设备报告了序列号
uid = sha1("kind|endpoint|serial")[:16]      # 没报告时按机器 + serial
```

因此同一台手机即使通过两个 agent 被发现，在设备列表里**也只有一条记录**。


### 部署设备侧 agent（自包含）

远程机器上只需要 `python3` 和 `adb`：

```bash
# 在本项目目录执行（该机器需已开着旧的 agent 或 ssh 可达）
./scripts/install-agent.sh <机器IP> <旧agent端口> <新agent端口>

# 或者手动：把 agent/agent.py 拷过去运行
python3 agent/agent.py --host 0.0.0.0 --port 18077
```

`install-agent.sh` / `deploy_agent.py` 会在传输前检查远端 `python3`（≥3.10）和
`adb`，缺失时直接给出安装提示；启动后再用 `adb devices` 自检。

完成后在 **远程机器连接配置**页点「新增机器」，填该机器 IP 与 agent 端口。

## 接入真实远程机器

```bash
./scripts/connect-remote-machine.sh <host> <port> [name]
```

脚本会：检查连通性 → 登记机器 → 探测 → 同步设备 → 若无设备则打印启动模拟器的命令。

**关键**：远程机器上必须真的有设备在线。以 `192.168.114.130` 为例，它装了 Android SDK
（`~/Android/Sdk`）、有 AVD `Pixel_6`、`/dev/kvm` 可用，但**模拟器默认不启动**：

```bash
cd ~/Android/Sdk/emulator
nohup ./emulator -avd Pixel_6 -no-window -no-audio -no-boot-anim \
    -gpu swiftshader_indirect > /tmp/emu.log 2>&1 &
# 等 boot 完成再同步
adb -s emulator-5554 shell getprop sys.boot_completed   # 期望 1
```

> boot 完成前设备会显示 `offline` 或短暂掉线，此时同步/上传会失败。

## 需求对应

| # | 需求 | 实现位置 |
|---|---|---|
| R1 | 获取本机 / 远程机器的 Android 设备并访问文件目录结构，需要 su 时报错 | `backend/app/transports/`（本机 adb / 远程 MDD 引擎）、`backend/app/api/routes/files.py` |
| R2 | 拉取 / 上传数据 | `backend/app/services/transfer.py`、`routes/files.py`（pull / push / upload） |
| R3 | 前台每隔几秒查询设备状态、CPU、内存 | `backend/app/services/metrics.py`、`routes/devices.py`，前端 `POLL_INTERVAL_MS = 3000` |
| R4 | 每台机器绑定唯一标识并用它做生命周期操作 | `backend/app/services/registry.py`（`build_device_uid`）、`routes/devices.py`（connect / disconnect） |

## 目录

```
AndroidFileExplorer/
├── docs/
│   ├── PLAN.md             实施计划（需求拆解 / 架构 / 里程碑 / 事故复盘）
│   ├── FEATURES.md         功能点实现清单（勾选制，随交付更新）
│   ├── TECHNICAL_IMPLEMENTATION.md  技术实现方案（模块 / 链路 / 关键决策）
│   ├── AGENT.md            设备侧 agent 使用与运维指南
│   ├── api-contract.md     HTTP 契约
│   └── 20261112-界面功能不足与修复清单.md  本轮界面/功能修复清单
├── agent/                   设备侧代理（仅标准库 + adb，可单独部署）
├── backend/                FastAPI + SQLModel
│   ├── app/
│   │   ├── transports/     base 抽象 + local_adb + mdd_client + remote_engine
│   │   ├── services/       registry / file_service / metrics / transfer / transport_resolver / preflight
│   │   ├── api/auth.py     可选 Bearer 鉴权中间件
│   │   ├── api/request_id.py  请求 ID 中间件（X-Request-ID）
│   │   ├── api/routes/     devices / files / engines / settings / system
│   │   └── main.py
│   ├── scripts/            e2e_demo / live_server_check / render_check_with_device / real_device_check
│   └── tests/              192 个 pytest 用例
├── frontend/               Vite + React 19 + antd 5 + TanStack Query + TanStack Router
│   └── src/
│       ├── api/            类型 + 请求封装（含 token 引导）
│       ├── components/     ErrorBoundary（全局兜底）
│       ├── hooks/          刷新间隔 / 环境自检等共享 query hook
│       ├── utils/          格式化工具
│       ├── layout/         AppLayout（竖排菜单：远程机器 / 设备资源 / 文件浏览 / 设置）
│       ├── pages/machines/ 远程机器连接配置
│       ├── pages/devices/  设备资源管理
│       ├── pages/files/    文件浏览（设备选择 + 目录浏览）
│       ├── pages/settings/ 平台设置 + 环境自检
│       └── router.tsx      五条业务路由 + 根重定向
├── .github/workflows/ci.yml  CI（后端测试 + 前端 vitest/tsc/build）
└── scripts/
    ├── check-env.sh        本地环境检查（Python / 依赖 / node / adb）
    ├── start.sh            后台启动 / 停止
    ├── dev.sh              前台启动
    ├── verify.sh           完整验证门禁（6 步）
    ├── render-check.sh     真实浏览器逐页渲染检查
    ├── file-browser-check.sh/.mjs  文件浏览交互检查（CDP，零依赖）
    ├── connect-remote-machine.sh   一键接入远程机器
    └── install-agent.sh           一键部署设备侧 agent（自包含）
```

## 环境前置

| 组件 | 用途 | 缺失时的表现 |
|---|---|---|
| Python 3.10+ / `.venv` | 后端运行时 | 后端无法启动 |
| Node.js 20+ / npm | 前端开发服务器 | 前端无法启动 |
| `adb`（Android platform-tools） | 本机设备发现 | 仅影响本机设备；远程机器仍可用 |
| `asyncssh`（Python 包） | SSH 直连接入 | SSH 直连不可用；agent 接入不受影响 |

启动和部署前可以手动检查：

```bash
./scripts/check-env.sh                 # 后端 + 前端
./scripts/check-env.sh --backend-only  # 仅后端
```

后端还会在启动时记录环境警告，并提供 `GET /api/v1/system/check` 给设置页的
「环境自检」卡片使用；`adb` / `asyncssh` 属于可选能力，缺失不会阻止其他功能启动。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
./scripts/check-env.sh
./scripts/start.sh
```

## 验证

```bash
./scripts/verify.sh
```

| 步骤 | 内容 | 通过标准 |
|---|---|---|
| 1 | 后端单元 + 集成测试（含 agent、认证、设备标识、文件操作、root 切换、连接池、缓冲读） | 192 passed |
| 2 | 后端端到端（TestClient） | 全部通过 |
| 3 | 后端端到端（真实 uvicorn + 真实 HTTP） | 全部通过 |
| 4 | 前端类型检查 + 生产构建 | `tsc -b` + `vite build` 通过 |
| 5 | **真实浏览器逐页渲染检查（5 页）** | 50 项断言全 PASS |
| 6 | **文件浏览交互检查**（路径直达 / 面包屑 / 越权提示） | 11 项断言全 PASS |

前端另有单元测试：`cd frontend && npm test`（vitest + happy-dom，18 用例：
格式化工具 / 请求层错误提取 / `?token=` 引导 / ErrorBoundary）。

CI：`.github/workflows/ci.yml` 在 push / PR 上自动跑「后端 pytest + e2e smoke」
与「前端 vitest + tsc/build」两个 job。

带设备场景另跑一轮（`backend/scripts/render_check_with_device.py`）：5 页 54 项断言全 PASS。

第 6 步用 Chrome DevTools Protocol 直连（**零 npm 依赖**，不装 puppeteer），
验证的是真实交互：填路径 → 跳转 → 点面包屑 → 看到权限告警。

真实设备验收（需要一台开着 agent_server 的机器和在线设备）：

```bash
cd backend && ../.venv/bin/python scripts/real_device_check.py --host 192.168.114.130 --port 12345
```

### 第 5 步为什么不能省

`vite build` 通过 **不等于** 页面能渲染。

本工程曾出现：`react` 装成 19.2.8、`react-dom` 装成 19.2.7（`^19.2.0` 分别解析到不同版本），
`npm run build` 全绿、TypeScript 全绿、curl 也能拿到 200 —— 但浏览器里 React 直接抛
`Incompatible React versions`，页面一片空白。

只有把页面放进真实浏览器、断言 `#root` 里有实际内容，才能发现这类问题。
`scripts/render-check.sh` 用 `google-chrome --headless --dump-dom` 实现，零 npm 依赖。

### root 提权

未 root 的设备访问不了 `/data`、`/system` 等目录。文件浏览页提供「尝试 root」按钮：

| 操作 | 接口 | 效果 |
|---|---|---|
| 尝试 root | `POST /api/v1/devices/{uid}/root` | `adb root` 重启 adbd，之后 shell 是 `uid=0(root)` |
| 取消 root | `POST /api/v1/devices/{uid}/unroot` | `adb unroot` 恢复普通用户；界面上的按钮会自动切换 |

设备的 root 状态由 `id` 命令实时探测，**显示在设备表里**（金色 `root` 标签）。
生产版本设备会拒绝（`adbd cannot run as root in production builds`），此时接口返回 400 并说明原因。

### 权限（su）行为

设备上访问需要 root 的路径（`/system`、`/data` 等）时：

- 后端返回 **403**，message 为 `Permission denied: /data/`;
- 该路径的搜索会被**跳过**，其余路径的搜索照常返回；
- **不会**因此把整台设备标记为异常 —— 路径权限是局部事实，不是设备生命周期事实。

## 配置

见 `backend/.env.example`。关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SERVER_HOST` | 0.0.0.0 | API 绑定地址（改成 0.0.0.0 才能被外部访问） |
| `SERVER_PORT` | 8010 | API 端口 |
| `ADB_PATH` | adb | 本机 adb 可执行文件 |
| `ENGINE_CONNECT_TIMEOUT` | 5 | 远程引擎连接超时（秒） |
| `ENGINE_COMMAND_TIMEOUT` | 30 | 远程引擎命令超时（秒） |
| `DEVICE_OFFLINE_THRESHOLD` | 2 | 连续多少次指标采集失败后把设备标记为离线/异常 |
| `UPLOAD_DIR` / `DOWNLOAD_DIR` | ./data/... | 文件落地目录 |
| `MAX_UPLOAD_BYTES` | 2GiB | 单次上传上限 |
| `STAGING_MAX_AGE_HOURS` | 24 | 落地/暂存文件保留时长（0 = 不清理） |
| `API_AUTH_TOKEN` | （空） | 非空时开启 API 鉴权：除 `/api/v1/health` 外都要求 `Authorization: Bearer <token>`；前端可用 `?token=` 登录，下载锚点把 token 放 query |
| `REMOTE_ENGINE_POOL_SIZE` | 4 | 每台远程机器并发命令连接数（坏连接自愈，满则排队） |

## 设计要点

- **传输层抽象**：`Transport` 抽象屏蔽「本机 adb」与「远程 MDD 引擎」的差异，
  上层服务（文件、指标、生命周期）完全一致。
- **agent_server 报文包装**：真实 agent_server 会把结果包成
  `命令执行成功 (返回码: 0) / 标准输出: / ====`，客户端必须剥离这层包装，
  否则 `adb devices -l` 会被解析成一台名叫「命令执行成功」的假设备。
- **toybox 兼容**：Android 的 toybox `ls` 没有 `-N` 选项，且对 symlink 参数
  只会「描述链接」而非「列出内容」，因此命令固定为 `ls -la <path>/`（补尾斜杠）。
- **唯一标识**：`uid = sha1(kind|endpoint|serial)[:16]`，跨重连稳定，跨机器不冲突。
- **CPU 用差分**：`/proc/stat` 是累计值，用两次采样差分算使用率；**首次采样返回 null**，
  不编造 0%。
- **一次 shell 往返**：CPU / 内存 / load average / 品牌全部在一次采样里取回（品牌单独一次调用，
  因为真机上 `getprop` 只有作为唯一命令时才能解析）。
- **大文件不阻塞**：所有传输与文件 IO 都 `asyncio.to_thread` 卸载出事件循环。
- **上传流式落盘**：multipart 分块写盘，2GiB 上限不再整体进内存。
- **root 状态缓存**：`id` 探测带 15s TTL，root/unroot 后自动失效，轮询不再每次打设备。
- **落地文件唯一命名 + 定期清理**：不同设备同名文件不互相覆盖；`STAGING_MAX_AGE_HOURS`（默认 24h）过期自动清。
- **同一台机器只登记一次**：`(kind, host, port)` 唯一索引，重复登记返回 400。
- **SQLite WAL**：并发同步任务写库不再有 `database is locked` 风险。
- **远程命令连接池**：每台机器维持 `REMOTE_ENGINE_POOL_SIZE` 条常驻 MDD 连接，
  不同设备的命令互不排队；坏连接自愈、机器刚失败时短暂跳过，传输走独立短连接。
- **请求 ID 追踪**：每个响应带 `X-Request-ID`，访问日志同格式输出 `rid=...`，
  用户报错时可直接定位那一次请求的全部日志。
- **中间件顺序**：CORS 最外层（浏览器 preflight 不带 Authorization 头），
  鉴权居中，请求 ID 最内层（401 也带 ID）。
- **前端 URL 即状态**：文件浏览路径写进地址栏（可分享 / 前进后退），
  大目录虚拟滚动、多选批量删除、全局 ErrorBoundary 兜底。
- **react / react-dom 锁死同版本**：`package.json` 用精确版本 + `overrides`，
  避免 caret 分别解析出不同版本导致运行时白屏。
- **环境自检**：启动只记录 warning，`GET /api/v1/system/check` 供设置页展示；
  `adb` / `asyncssh` 是可选能力，缺失不阻止后端启动，设置页与设备页给出修复指引。
