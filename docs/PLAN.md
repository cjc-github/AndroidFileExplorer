# AndroidFileExplorer 实施计划

> 目标：实现 frontend-api 项目中「Android 文件资源 / 文件管理」这块能力，只做核心功能（不含登录注册）。

## 0. 需求拆解（来自用户 4 条要求）

| # | 需求 | 验收口径 |
|---|------|---------|
| R1 | 获取远程机器 / 本机机器中的 Android 设备，访问其文件目录结构；需要 su 时报错 | 能列出设备（含唯一标识）、能按路径列出目录项；越权路径返回明确的权限错误而非 500 |
| R2 | 拉取 / 上传数据到这些机器 | 设备文件 → 本地下载可用；本地文件 → 设备上传可用 |
| R3 | 前台每隔几秒查询设备运行状态、CPU、内存 | 指标接口可按间隔轮询，返回 cpu/mem 百分比与原始值 |
| R4 | 每台机器绑定唯一单独标识，用它做生命周期绑定（是否连接等操作） | 设备唯一 ID 稳定可复现；按 ID 可查状态/重连/断开 |

## 1. 现状勘察结论（证据来源）

- 前端：`/home/test/tcl/frontend-api`，React 19 + Vite 8 + TanStack Router/Query，设备页 `src/pages/device/`（devices / connect / device-file-explorer）。
- 后端：`/home/test/tcl/platform-api`，FastAPI + SQLModel(SQLite)，设备路由 `app/api/routes/device.py`。
  - 本机设备：`app/services/device.py` 走 `adb` 子进程（`adb -s <target> shell ls -Al`）。
  - 远程机器：`apk_survey.fuzzing.device_client.DeviceClient`（MDD TCP 协议：`CMD` 行协议以 `===END===` 收尾，`FILE_PUSH` 推送，`FILE_PULL` 拉取）。
  - 已有的权限处理：输出含 `Permission denied` → `PermissionError` → HTTP 403。
- 环境事实（本 sprint 实测）：
  - `adb` = `/usr/bin/adb`（1.0.41），`adb devices` 当前**无设备**。
  - 远程引擎 `192.168.114.130:12345` **不可达**（`No route to host`）。
  - Python 3.10.12 可用，pip 可达 PyPI；Node 22（nvm）+ npm 可用。
  - 结论：真实设备端到端联调在当前环境不可行 → 传输层必须抽象，验证靠「假传输层 + 假 MDD 服务端」，代码按真实协议实现。

## 2. 架构：The Algorithm（质疑→删除→简化→加速→自动化）

1. **质疑需求**：不重造 platform-api 全家桶。新工程只保留 4 条要求必须的最小内核：传输层、设备注册表、文件服务、指标采集、HTTP API。
2. **删除多余步骤**：删除登录注册、DB 用户/会话、测试目标、函数分析、运行调度、NDK 等一切非核心依赖。数据落 SQLite 两张表（engines / devices）。
3. **简化**：传输层一个抽象 `Transport`（list_devices / shell / list_dir / pull / push / connect / disconnect），两种实现：
   - `LocalAdbTransport`：本机 adb（subprocess 参数数组，不拼 shell 字符串）。
   - `RemoteEngineTransport`：远程机器 MDD 协议（自实现最小客户端，不依赖 apk_survey 安装）。
4. **加速**：指标采集用「一次 shell 往返读 `/proc/stat` + `/proc/meminfo`」，CPU 用上次采样累计值做差分，避免阻塞式 1s 采样。
5. **自动化**：`pytest` 覆盖传输层契约、权限错误映射、唯一标识稳定性、生命周期状态机、MDD 协议推拉回环；前端 `tsc -b && vite build` 作为交付门禁。

## 3. 工程结构

```
AndroidFileExplorer/
├── docs/
│   ├── PLAN.md                  # 本计划
│   └── api-contract.md          # HTTP 契约
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + lifespan
│   │   ├── config.py            # 配置
│   │   ├── logger.py
│   │   ├── core/response.py     # BaseResponse 统一包装
│   │   ├── db/session.py
│   │   ├── models/device.py     # DeviceRecord / EngineRecord
│   │   ├── schemas/device.py    # Pydantic 出入参
│   │   ├── transports/
│   │   │   ├── base.py          # Transport 抽象 + 异常 + ls 解析
│   │   │   ├── local_adb.py     # 本机 adb
│   │   │   ├── mdd_client.py    # MDD TCP 最小客户端（CMD/PUSH/PULL）
│   │   │   └── remote_engine.py # 远程机器传输层
│   │   ├── services/
│   │   │   ├── registry.py      # 设备注册表：唯一 ID + 生命周期状态机
│   │   │   ├── file_service.py  # 目录列举 / 搜索
│   │   │   ├── metrics.py       # CPU / 内存采集与差分
│   │   │   └── transfer.py      # 拉取 / 上传
│   │   └── api/routes/{devices,files,engines}.py
│   └── tests/                   # pytest（含 fake transport / 假 MDD 服务端）
└── frontend/
    ├── package.json / vite.config.ts / tsconfig.json
    └── src/{api,pages/{devices,files},components}
```

## 4. 设备唯一标识与生命周期（R4）

- **唯一 ID**：`device_uid = sha1(f"{transport_kind}:{endpoint}:{serial}")[:16]`
  - 同一台物理设备重连后 ID 不变（生命周期绑定靠它）；
  - 不同机器 / 不同序列号绝不冲突；
  - 落库唯一索引，重复出现走 upsert。
- **生命周期状态机**：`online ⇄ offline`，另有 `unauthorized`（设备未授权）、`permission_denied`（需要 su）、`error`。
  - `online` → 心跳刷新失败或 `adb devices` 不再列出 → `offline`；
  - 权限错误 → `permission_denied`，不误判为 offline；
  - 状态变更写入 `last_error` / `last_seen`，可追溯。

## 5. 指标采集（R3）

- 单次 shell：`cat /proc/stat; echo '---AFE---'; cat /proc/meminfo`。
- CPU：累计 jiffies 差分 `1 - Δidle/Δtotal`，保留上次采样点；首次采样返回 `null`（无基线不编数）。
- 内存：`MemTotal` / `MemAvailable`（缺失时回退 `MemFree+Buffers+Cached`）→ used、total、percent。
- 前台每 3 秒轮询（可配 `METRICS_INTERVAL_SECONDS`）。

## 6. API 契约（详见 docs/api-contract.md）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/engines` | 远程机器列表 |
| POST | `/api/v1/engines` | 新增远程机器并探活 |
| GET | `/api/v1/devices` | 设备列表（`?refresh=1` 触发同步） |
| GET | `/api/v1/devices/{uid}` | 设备详情 + 指标 |
| POST | `/api/v1/devices/{uid}/connect` | 重连该设备（生命周期操作） |
| POST | `/api/v1/devices/{uid}/disconnect` | 断开该设备 |
| GET | `/api/v1/devices/{uid}/metrics` | 单次指标采样 |
| GET | `/api/v1/devices/{uid}/files?path=` | 目录结构 |
| GET | `/api/v1/devices/{uid}/files/search?keyword=` | 关键字搜索 |
| POST | `/api/v1/devices/{uid}/files/pull` | 拉取（设备→落地） |
| POST | `/api/v1/devices/{uid}/files/push` | 上传（本地→设备） |

## 7. 里程碑与验收

| 里程碑 | 交付物 | 验收证据 |
|---|---|---|
| M0 计划与骨架 | 本计划 + 目录/依赖就绪 | 目录树 + 依赖安装日志 |
| M1 传输层 | base/local_adb/mdd_client/remote_engine | pytest：MDD 假服务端 push/pull 回环、ls 解析、权限错误映射 |
| M2 注册表与生命周期 | registry + 唯一 ID + 状态机 | pytest：ID 稳定性、online→offline 转换、permission_denied 不误判 |
| M3 指标 | metrics 服务 | pytest：CPU 差分、内存解析、缺失字段降级 |
| M4 HTTP API | routes + 响应包装 | pytest + curl 实跑各端点输出 |
| M5 前端 | 设备列表(轮询) + 文件浏览器 + 上下传 | `tsc -b && vite build` 通过 |
| M6 端到端 | 假设备全链路 | 启动服务 → curl 全接口 → 贴输出 |

### 验收结果（2026-11-10 全部通过）

| 验证 | 命令 | 结果 |
|---|---|---|
| 单元 + 集成 | `cd backend && ../.venv/bin/python -m pytest -q` | **34 passed** |
| 端到端（TestClient） | `../.venv/bin/python scripts/e2e_demo.py` | 全部通过 |
| 端到端（真实 uvicorn + 真实 HTTP） | `../.venv/bin/python scripts/live_server_check.py` | 全部通过 |
| 前端 ↔ 后端联调（vite 代理） | `../.venv/bin/python scripts/frontend_live_check.py` | 全部通过 |
| 前端类型检查 + 生产构建 | `cd frontend && npm run build` | `tsc -b` + `vite build` 通过 |

本 sprint 修复的 6 个真实缺陷（均已固化为回归测试）：

1. `ls -l` 解析器按 3 字段切分日期，导致文件名整体错位 → 按 7 个固定字段定位名称列。
2. `MetricsSample` 是 `slots=True` dataclass，动态写 `_raw_total` 会 AttributeError → 提成正式字段。
3. 传输层解析器只认 `local`/`remote` 两种 kind，设备记录里的 kind 描述的是设备而非机器 → 改为按 `engine_id` 查表。
4. `_local_engine_id()` 少写 `await`，协程从未被执行 → 修正为 async。
5. **路径级权限不足被升级成设备级状态**，一次访问 `/system` 就锁死整台设备 → 路径权限只影响该次请求（403）。
6. 测试用 `asyncio.get_event_loop()` 在 pytest-asyncio 下会抛 RuntimeError → 改用 `asyncio.run()`。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 当前环境无真实设备/引擎 | 传输层抽象 + fake 实现；真实 adb 路径用假 `adb` 可执行脚本做单测 |
| MDD 协议细节与 apk_survey 有偏差 | 严格按 `apk_survey/fuzzing/device_client.py` 的报文格式实现，并用假服务端做回环测试 |
| su 权限错误被误报为 500 | 传输层统一抛 `PermissionDeniedError`，路由层映射 403 + 明确 message |
| 大文件传输阻塞事件循环 | 传输与文件 IO 全部 `asyncio.to_thread` 卸载 |
| CPU 首采样无基线 | 返回 `null` 而非编造 0% |
| 远程引擎的 pull 落在引擎机器上 | 传输任务返回 `stored_at` 明确落地位置，不谎称落在 API 服务器 |

## 9. 明确不做（对齐用户「只考虑核心功能」）

登录注册 / 用户会话 / 测试目标 / 函数分析 / 运行调度 / NDK / 模型配置 / 系统设置 —— 一律不实现。
