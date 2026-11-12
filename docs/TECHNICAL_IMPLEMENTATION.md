# AndroidFileExplorer 技术实现文档

> 本文描述项目的具体实现技术与逻辑：技术选型、架构分层、每个模块的实现细节、
> 关键工程问题的解法。配合 `README.md`（功能视角）与 `docs/PLAN.md`（实施计划）阅读。

---

## 1. 技术栈总览

| 层 | 技术 | 版本 | 用途 |
|---|---|---|---|
| 后端框架 | FastAPI | 0.115.0 | 异步 HTTP API + 自动 OpenAPI 文档 |
| ASGI 服务 | uvicorn[standard] | 0.32.0 | 生产/开发共用服务器 |
| ORM | SQLModel | 0.0.22 | Pydantic + SQLAlchemy 二合一模型 |
| 数据库 | SQLite + aiosqlite | 0.20.0 | 零运维持久化（engines / devices / settings 三张表） |
| 日志 | loguru | 0.7.3 | 结构化日志 |
| 上传解析 | python-multipart | 0.0.20 | multipart 文件上传 |
| 配置 | python-dotenv + dataclass | - | `.env` 环境变量覆盖 |
| 前端框架 | React | **19.2.7（精确锁定）** | UI |
| 构建工具 | Vite | 7.x | dev server（:10012）+ 生产构建 |
| 语言 | TypeScript | 5.9.x | 类型安全 |
| UI 组件 | antd 5 + @ant-design/icons | 5.27.x | 表格 / 表单 / 反馈 |
| 数据获取 | @tanstack/react-query | 5.x | 轮询、缓存、mutation |
| 路由 | @tanstack/react-router | 1.x | 6 条路由（代码式路由） |
| HTTP 客户端 | axios | 1.14.x | 统一请求封装 |
| 设备侧 agent | Python 标准库 | 3.10+ | `socketserver.ThreadingTCPServer`，零第三方依赖 |
| 测试 | pytest + pytest-asyncio | 8.x | 192 用例（含假 MDD 服务端） |
| 前端测试 | vitest + happy-dom | 4.x | 18 用例（请求层 / 工具 / ErrorBoundary） |
| 渲染验证 | Chrome DevTools Protocol + headless chrome | - | 零 npm 依赖的真实浏览器检查 |

**核心设计原则：自包含。** 除本机 `adb` 外不依赖任何外部组件；远程机器上只需
`python3` + `adb`，设备侧代理（`agent/agent.py`）属于本项目。

---

## 2. 总体架构

```
浏览器（React 19, :10012）
   │  /api/*（vite dev 代理 → 127.0.0.1:8010）
   ▼
后端 FastAPI（:8010）
   │
   ├─ TransportResolver ──── engine_id → Transport 实例（缓存）
   │
   ├─ LocalAdbTransport ─────▶ 本机 adb server（subprocess, 参数数组）
   │
   └─ RemoteEngineTransport ─▶ MDD TCP 连接池（常驻 + 熔断）
                                  ▼
                       agent/agent.py（远程机器, :18077）
                        │  只执行 adb 命令（白名单）
                        ▼
                        adb server（远程机器）──▶ Android 设备
```

分层职责：

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `backend/app/main.py` | lifespan（建库、登记本机引擎、启动自动同步循环）、中间件栈 |
| 路由 | `app/api/routes/` | HTTP 语义（状态码、错误映射），不含业务 |
| 服务 | `app/services/` | 业务逻辑（注册表、文件、指标、传输、机器解析） |
| 传输 | `app/transports/` | 抽象 `Transport` + 本机 adb / 远程 MDD 两种实现 |
| 模型 | `app/models/` `app/schemas/` | 持久化模型 / API 契约模型 |
| 前端 | `frontend/src/` | `api/` 请求层、`components/` 兜底组件、`pages/` 四大页面、`router.tsx` |
| agent | `agent/agent.py` | 远程机器上的 adb 桥接守护进程 |

---

## 3. 后端实现

### 3.1 配置（`app/config.py`）

- dataclass `Settings` 收拢全部可调参数；每个字段支持环境变量覆盖
  （`SERVER_HOST/SERVER_PORT/ADB_PATH/ENGINE_*_TIMEOUT/MAX_UPLOAD_BYTES...`）。
- `database_url` 默认 `sqlite+aiosqlite:///{DATA_DIR}/afe.db`，可用 `DATABASE_URL` 整体覆盖。
- `ensure_dirs()` 在 lifespan 里创建 `data/uploads`、`data/downloads`。
- 三个较新的开关（`.env.example` 与 README 配置表同步）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `REMOTE_ENGINE_POOL_SIZE` | 4 | 每台远程机器并发命令连接数（连接池封顶） |
| `STAGING_MAX_AGE_HOURS` | 24 | 落地/暂存文件保留时长（0 = 不清理） |
| `DEVICE_OFFLINE_THRESHOLD` | 2 | 连续指标失败多少次后把设备标记离线/异常 |
| `API_AUTH_TOKEN` | （空） | 非空则开启 API 鉴权（见 3.10） |

### 3.2 数据层（`app/db/session.py` + `app/models/`）

三张表：

- **engines**：承载 ADB server 的机器。`kind`（local/remote）、`host/port`、
  `token`（agent 共享密钥）、`status`（unknown/connected/disconnected/error）。
- **devices**：设备注册表，主键是 `uid`（见 3.6）。含静态属性
  （brand/cpu_abi/android_version/api_level）、生命周期 `status`
  （online/offline/unauthorized/permission_denied/error）、`is_rooted`。
- **settings**：平台设置键值表（如 `auto_root`、同步间隔），改配置即时生效。

**轻量迁移**（`_migrate_columns`）：SQLModel 的 `create_all` 只建表不补列，
模型加字段会让老库报 `no such column`。启动时用 `PRAGMA table_info` 对比
模型与实际列，缺的列用 `ALTER TABLE ... ADD COLUMN` + 标量默认值补齐，
不需要引入 alembic。

**WAL 模式**（`_enable_wal`）：SQLite 默认 `journal_mode=delete`，
写操作拿库级排他锁——后台同步循环与前台轮询并发写时会撞
`database is locked`。启动时用 `engine.connect()` + `isolation_level="AUTOCOMMIT"`
执行一次 `PRAGMA journal_mode=WAL`（WAL 是**库级持久属性**，且不能在事务内执行），
配合 `busy_timeout=10s` 让并发写排队而不是直接报错。

### 3.3 统一响应（`app/core/response.py`）

`BaseResponse[T] = {code, message, data, timestamp}`，泛型 Pydantic 包装；
所有路由返回 `BaseResponse.success(data, message)`，错误走 FastAPI 原生
`HTTPException`（`{"detail": ...}`），前端 `extractErrorMessage` 同时兼容两种形态。

### 3.4 传输层抽象（`app/transports/base.py`）

`Transport(ABC)` 是整个系统的脊柱，屏蔽「本机 adb」与「远程 MDD」差异：

```python
class Transport(ABC):
    kind: str                      # "local" / "remote"
    async list_devices() -> list[DeviceEntry]
    async shell(serial, command, timeout) -> str
    async list_dir(serial, path) -> list[FileEntry]
    async pull(...) / push(...) -> TransferResult
    async connect(serial) / disconnect(serial) -> tuple[bool, str]
    # 通用实现（子类免费获得）：
    async metrics(serial)          # 一次 shell 读 /proc 三件套 + 品牌
    async is_rooted(serial)        # shell `id` → uid=0(root)；**15s TTL 缓存**
    async set_root(serial, bool)   # adb root/unroot + 等设备回归 + 清 root 缓存
    async device_props(serial)     # getprop 一次性 dump（按设备缓存）
    async device_brand(serial)     # ro.product.brand（只读一次，缓存）
    async device_hardware_id(serial)  # ro.serialno（uid 的根）
    async mkdir/rename/remove(...) # 设备侧文件操作（带路径安全检查）
```

**错误分类体系**——命令输出 → 类型化异常（`classify_output`）：

| 输出特征（小写匹配） | 异常 | HTTP 映射 |
|---|---|---|
| `permission denied / requires root / su: not found / inaccessible or not found ...` | `PermissionDeniedError` | 403 |
| `device unauthorized` | `DeviceUnauthorizedError` | 403 + 状态改 unauthorized |
| `device offline / connection reset / broken pipe / connection closed / cannot connect to daemon ...` | `DeviceOfflineError` | 400 + 状态改 offline |

**误判面刻意收窄**：裸 `"not found"` / `"closed"` 曾把正常输出（如文件不存在、
设备自述文本）误判成掉线，已从 offline 标记中移除——宁可 502 也不把健康设备
踢出注册表。

**路径安全（防 shell 注入）**——`assert_safe_device_path`：

- `adb shell` 会在**设备上再解析一次命令**，agent 侧的引号到设备已失效，
  `/sdcard/x$(id)` 会执行 `id`。
- 因此维护 `FORBIDDEN_PATH_CHARS = {;|&$`<>()"' 换行 制表 *?[]{}!#~%^,}`，
  路径里出现任一字符直接抛 `UnsafePathError`（→ 400），仅放行无害字符。
- 所有 `mkdir/mv/rm` 用 `shlex.quote` 包一层（本地 adb 参数数组场景双保险）。

**toybox 兼容（`directory_listing_path`）**：Android 的 `ls` 没有 `-N`，
且对 symlink 参数只「描述链接」不「列出内容」，`ls /sdcard` 只显示链接本身。
命令固定 `ls -la <path>/`（**补尾斜杠**）强制列目录内容。

**`ls -l` 解析（`parse_long_listing`）**：权限位正则
`^[-dlbcps][-rwxsStTlL+]{9}` 判定有效行；名字列从第 8 个字段开始（toybox），
busybox 布局是第 9 个（带年份）——按是否匹配 ISO 日期动态判定；
`modified_at` 一并解析（toybox ISO `2024-01-02 13:45:00.123` / busybox
`Jan 2 13:45`，缺年份按当前年补齐）；`l` 开头的行按 ` -> ` 切出链接目标；
`ls:` 开头的行抛 `PermissionDeniedError`；`total N` / `.` / `..` 跳过。

**指标单次往返（`METRICS_COMMAND`）**：

```sh
cat /proc/stat; echo '---AFE---'; cat /proc/meminfo; echo '---AFEPROPS---'; cat /proc/loadavg
```

用两个标记把一次 shell 输出切成三段（stat / meminfo / loadavg），避免三次
adb 往返。`getprop` 被刻意排除：真机上 `getprop` **只有作为 shell 唯一命令时
才能解析**，所以品牌（`ro.product.brand`）单独一次调用并缓存。

`parse_metrics` 解析细节：

- CPU：`cpu ` 行取 jiffy 数组，`total=sum`，`idle=user+nice`，`system=kernel+irq+softirq+steal`；
  原始累计值存进 `raw_*_jiffies` 供差分。
- 内存：`MemTotal` / `MemAvailable`（kB × 1024），used = total - available。
- loadavg：`"0.00 0.02 0.13 1/1284 6607"` 取前三个浮点。

**root 切换（`set_root`）**——两处坑的对策：

1. `adb root` 重启 adbd，**返回码恒为非 0**（即使成功）；且设备会掉线几秒。
   解法：输出里出现 `restarting adbd as root` → 视为成功，然后
   `_wait_until_online`（循环 `echo online`，30s 超时）等设备回来。
2. 生产版本回答 `adbd cannot run as root in production builds` → 返回
   `(False, 明确原因)`，接口层转 400。
3. `unroot` 对本来就没 root 的设备返回 `not running as root` → 也算成功
   （请求已满足）。

### 3.5 本机传输（`app/transports/local_adb.py`）

- `subprocess.run([...参数数组...])`，**绝不拼 shell 字符串**；超时 15s（pull/push 600s）。
- 通过 `ADB_SERVER_SOCKET=tcp:host:port` 环境变量指向指定 adb server。
- 所有阻塞调用 `asyncio.to_thread` 卸载，事件循环不被大文件 IO 阻塞。
- `parse_adb_devices` 解析 `adb devices -l`：serial / state / `key:value` 属性
  （model、brand、product、transport_id）。
- `connect` 成功判定：输出含 `connected to` 或 `already connected`（不依赖返回码）。

### 3.6 设备唯一标识（`app/services/registry.py::build_device_uid`）

```
有硬件序列号:  uid = sha1("hw|" + ro.serialno)[:16]
无硬件序列号:  uid = sha1(f"{kind}|{endpoint}|{serial}")[:16]
```

- 标识基于**设备自身**（`ro.serialno`），与「从哪台机器发现」解耦：
  同一台手机经两个 agent 发现，在列表里仍只有一条记录（uid 相同 → upsert 覆盖）。
- 无序列号的设备（如模拟器 serial 被多台机器复用）回退到 机器+serial，
  保证在该机器上跨重连稳定。
- **注册表状态机**（`upsert_device` / `sync_transport` / `mark_status`）：
  `adb devices -l` 的原始状态 → 归一化
  （`device→online`、`unauthorized→unauthorized`、`offline/bootloader/recovery→offline`）；
  同步时消失的设备标记 `offline`（不删除）；机器同步失败 → 引擎行记 `error`。

### 3.7 远程传输 + MDD 协议（`remote_engine.py` + `mdd_client.py` + `agent_protocol.py`）

**信封格式（agent_protocol.py）**——agent 把命令结果包成：

```
命令执行成功 (返回码: 0)          ← 正则 ^命令执行(成功|失败)\s*\(返回码:\s*(-?\d+)\)
--------------------------------------------------（50 个 -）
标准输出:                        或 错误信息:
<真实输出>

==================================================（50 个 =）
```

`unwrap_agent_output` 剥离信封返回 `(ok, 真实输出)`。**必须剥离**：否则
`adb devices -l` 会被解析出一台名叫「命令执行成功」的假设备。此模块独立成文件
是因为 base 和 mdd_client 都要用（避免循环导入）。

**MddClient**（同步 socket，`asyncio.to_thread` 包装）：

| 操作 | 线上格式 |
|---|---|
| CMD | 发 `<command>\n`，读到 `===END===` 为止 |
| AUTH | 连接后第一行 `AUTH <token>\n`，非 `OK` 开头即断开 |
| FILE_PUSH | `FILE_PUSH:{json}\n`（filename/file_size/remote_path/md5）+ 原始字节流 → 服务端回 `OK\n` |
| FILE_PULL | `FILE_PULL:{json}\n` → 回 `FILE_INFO:FILE:<name>\n` + `FILE_DATA:<name>:<size>:<md5>\n` + 字节流；客户端收满 size 字节后回 `OK\n`，md5 不符判失败 |
| PING / SHUTDOWN | `PONG` / agent 自行退出（部署不需要 ssh） |

**RemoteEngineTransport 的性能设计**：

1. **连接池**：每台机器保留最多 `REMOTE_ENGINE_POOL_SIZE`（默认 4）条持久
   MDD 连接——空闲复用、全忙再开新连接（封顶）、超时 30s 让路；**不同设备的
   命令不再在单条 socket 后排队**。坏连接用完即弃，`_opened` 计数收缩；
   故障熔断 `_failed_until` 语义不变。
2. **传输专用连接**：push/pull 仍各自开短连接（600s 长任务），不占命令池，
   大文件传输不会饿死轮询。
3. **agent 并发**：设备端 `CommandRunner` 从全局锁改为**信号量上限**
   （`--max-concurrent`，默认 8）——多台设备命令可并行，突发不炸机。
4. **熔断跳过**：失败后 `_failed_until = now + min(connect_timeout, 5s)`，
   熔断窗口内的请求立刻抛「暂时不可用」，不再等满超时；下一次成功 `_reset_failure`。
5. **连接自愈**：`MddConnectionError/TimeoutError` → 丢弃坏连接，
   下次调用自动重开。

### 3.8 设备侧 agent（`agent/agent.py`）

- `socketserver.ThreadingTCPServer`（daemon threads，allow_reuse_address），
  每连接一个 `AgentHandler`：认证 → 循环读一行 → dispatch → 写回。
- **只执行 adb 命令**（`parts[0] != "adb"` 直接拒绝）——agent 是设备桥不是通用 shell。
- adb 子进程同样用**参数数组**，输出超 512KB 截断，超时 120s（传输 600s）。
- 认证：`AUTH <token>` 首行，`hmac.compare_digest` 常数时间比较，失败回
  `ERROR: authentication failed` 并断开。
- `adb root/unroot` 特殊处理（`run_root_switch`）：成功文本 → 修正返回码为 0
  + `wait_for_device`（`adb get-state` 轮询直到 `device`），否则调用方只看到超时。
- 路径/serial 白名单与平台端一致（`FORBIDDEN_PATH_CHARS`、serial 正则
  `^[A-Za-z0-9._:-]{1,128}$`），防止经 agent 注入。
- `FILE_PUSH`：先落临时文件（边收边算 md5）→ `adb push` → 删临时文件；
  `FILE_PULL`：`adb pull` 到临时目录 → **两遍流式**（第一遍分块算 md5，
  第二遍分块写出，块 1MB）→ 三段式回发。全文件从不整读进内存。

### 3.9 服务层

- **`transport_resolver.py`**：`engine_id → Transport` 的工厂 + 缓存。
  解析顺序：显式 host/port（登记前探测）→ 已登记映射 → 本机兜底；
  `127.0.0.1:5037` 直接映射本机传输；远程实例按 `(host,port)` 缓存复用。
  删除/编辑机器时 `drop_engine` 会注销映射并关闭旧连接，避免继续使用旧密钥。
- **`preflight.py`**：环境自检（Python / 后端依赖 / asyncssh / adb / 数据目录）。
  启动时只记录 warning；`adb`、`asyncssh` 属于可选能力，缺失不会阻止后端启动。
- **`registry.py`**：见 3.6；`sync_transport` 用 **seen_uids 集合**做差分下线；
  `resolve(uid, transport_of)` 把设备记录与其传输实例绑定成 `DeviceContext`。
- **`file_service.py`**：
  - `list_dir`：排序（目录优先、名称不分大小写）；**面包屑从请求路径构建**
    而非父目录探测——未 root 设备上「父目录不可读、子目录可读」（`/data/local/tmp`）
    是常态，面包屑必须可用。
  - `search`：BFS + 每批 `SEARCH_CONCURRENCY=8` 个目录 **asyncio.gather 并发**；
    跳过伪文件系统 `SKIPPED_ROOTS`（`/proc /sys /dev /vendor /apex ...`）；
    每目录异常吞噬（权限目录不中断整体搜索）；`max_items` 默认 200（API 可提高到
    1000，前端「加载更多」）/ `max_depth=4 / max_dirs=400` 限流。全树搜索
    6.2s → 0.6s 的来源即「并发 + 跳过 /proc」。
- **`metrics.py`**：`MetricsCollector` 每设备保存一个 CPU 基线 + 一个缓存样本。
  `/proc/stat` 是累计 jiffy，CPU% = `(1 - Δidle/Δtotal) × 100`（夹在 0..100）；
  **首次采样返回 `None`**（无基线，不编造 0%）；缓存 TTL 2.5s——前端默认 3s 轮询
  周期内同一设备只打一次 shell。**它是进程级全局单例**（`get_metrics_collector`），
  缓存按 uid 命中：写测试时 uid 在用例间相同，必须先 `collector.reset(uid)`，
  否则命中上一次的缓存导致并发断言假失败。另维护每台设备的连续失败计数
  （`record_failure` / `record_success`）：可选/权限错误立即降级，离线与通用
  错误连续 2 次才降级，避免 adbd 重启窗口被误判为离线。
- **`transfer.py`**：落地文件**唯一命名** `<名>_<12hex>.<后缀>`（两台设备同名
  文件不再互相覆盖，`stored_at` 是真实落地路径）；`_sweep` 过期清理
  （`STAGING_MAX_AGE_HOURS` 默认 24h，0 = 关闭，挂在 `ensure_dirs` 里）；
  `stage_upload_stream` **流式落盘**（1MB 分块，超 `MAX_UPLOAD_BYTES` 即中断
  并删半成品）替代原先把 multipart 整体读进内存的 `stage_upload`。

### 3.10 API 路由层

**engines（`/api/v1/engines`）**：列表（附每机设备数、`has_ssh_password` 布尔）；
新增机器**先登记后探测**，探测失败不回滚（返回「机器已登记，但探测失败」）；
`PATCH /{id}` 编辑名称/地址/端口/密钥/SSH 凭据/adb 路径，保留设备记录，密钥与
密码留空表示不修改，保存后关闭旧 transport 并用新配置重新探测；`test` 现场探测
回设备数；删除级联删设备记录并清理 transport 缓存，`kind=local` 拒绝删除；
**`(kind, host, port)` 唯一索引** + API 层重复登记 400（「该机器已登记」），
启动时 `init_db` 自动去重旧数据（保留最老行、设备重新指向存活行）。

**devices（`/api/v1/devices`）**：

| 路由 | 逻辑要点 |
|---|---|
| `GET /` | `refresh=true` 先并发同步所有机器；在线设备的指标采样**并发**（`asyncio.gather`，每任务独立 DB session——共享 session 的写入互撞）；指标走 2.5s 缓存、root 探测走 15s TTL 缓存，结果回写 DB；采样失败会把状态降级（permission/unauthorized 立即，offline/通用错误连续 2 次），并支持全量同步恢复 |
| `GET /{uid}` | 详情 + **强制**重新采样（`force=True` 绕过缓存）；失败同样降级状态 |
| `GET /{uid}/metrics` | 前端轮询点；异常时改写生命周期（permission_denied / unauthorized / offline） |
| `PATCH /{uid}` | 改设备别名 |
| `POST /batch/root` | 多选批量 root/unroot，`asyncio.gather` 并发、逐台返回 `BatchResultItem` |
| `POST /{uid}/root|/unroot` | `set_root` → `collector.reset`（旧连接的指标作废）→ `is_rooted` 实测回写 |
| `POST /{uid}/connect|/disconnect` | `adb connect` 失败**不改 offline**（USB/模拟器 serial 不适用此命令，误标会把健康设备踢出注册表），只标 `error`；disconnect 成功标 offline |

**files（`/api/v1/devices/{uid}/files`）**：

| 路由 | 逻辑要点 |
|---|---|
| `GET ""` | 目录列表；权限错误 → 403（带引导文案「可在路径框输入更深层的可访问路径」），路径级 403 **不改设备生命周期** |
| `GET /search` | 关键字搜索（复用 FileService.search）；支持 `limit`（默认 200，最大 1000） |
| `GET /preview` | 文本 / 图片预览（≤2MB；符号链接和目录拒绝，二进制提示下载） |
| `GET /download` | 设备 → agent/本机落地 `download_dir`（唯一命名，`stored_at` 为真实落地路径）→ `FileResponse` 以附件形式流给浏览器（锚点 `<a download>`；开启鉴权时锚点把 token 放 query） |
| `GET /archive` | 目录递归打包 zip（≤200 文件 / 200MB / 6 层，受保护目录与符号链接写入 `_download_errors.txt`） |
| `POST /archive-selection` | 选中文件 / 目录合并打包 zip，重叠项去重 |
| `POST /pull` `POST /push` | pull 的字节流最终落在 API 服务器暂存目录（远程链路：设备 → agent 临时目录 → `FILE_PULL` 回传 → 本地落盘），`stored_at` 为真实本地路径；push 后 `stored_at` 写设备目标路径 |
| `POST /upload` | multipart → **流式落盘 staging**（分块 + 上限校验）→ `adb push` → 暂存文件用后即删 |
| `POST /mkdir|/rename|/remove` | 透传 transport 通用实现；`mv a a` 前置拒绝（避免设备上 cryptic "Invalid argument"） |
| `POST /copy` | 设备侧 `cp -r` 复制文件 / 目录 |
| `POST /permissions` | `chmod`（3~4 位八进制）+ `chown`（owner/group，格式校验） |

**错误映射矩阵**（`_guard` 统一处理）：
`PermissionDeniedError→403`（状态不变）、`DeviceUnauthorizedError→403`（状态改）、
`DeviceOfflineError→400`（状态改）、`UnsafePathError(ValueError)→400`、
`no such file→404`、`not a directory→400`、其余 `TransportError→502`。

**生命周期循环（`main.py::_auto_sync_loop`）**：lifespan 启动后台任务，按
平台设置间隔（0 = 停用，5s 重读配置实现不停机切换）执行 `_sync_all_machines`；
auto-root 钩子在同步末尾执行一次（避免重复重启 adbd，逐台失败不影响其它）。
**每个机器的同步任务用独立 DB session**——共享 session 的并发提交会互撞
（"commit() can't be called here"）。

**中间件栈**（`main.py`）——Starlette 的 `add_middleware` 插到 index 0，
所以**最后添加的最外层**：

```python
app.add_middleware(AuthTokenMiddleware, ...)  # 最先加 = 最内层
app.add_middleware(RequestIdMiddleware)       # 居中
app.add_middleware(CORSMiddleware, ...)       # 最后加 = 最外层
```

- **CORS 最外层**：浏览器 preflight（`OPTIONS`）不带 `Authorization` 头，
  CORS 必须在鉴权之外，否则预检先被 401 拦掉；CORS 也在最外层给
  所有响应（包括 401）补 `Access-Control-Allow-Origin`，跨域前端才能读到拒绝原因。
- **请求 ID 居中、鉴权最内层**：鉴权中间件应答 401 时**不会**调用更内层的栈，
  所以请求 ID 必须在鉴权之外才能盖到 401 响应——这一点容易想反，
  曾把请求 ID 放最内层导致 401 无 ID（实测 `rid_check` 抓出后修正）。
  用五个排列组合逐一实测的结论：`CORS → 请求 ID → 鉴权` 是唯一同时满足
  「preflight 放行」「401 带 ID」「401 带 CORS 头」的顺序。

### 3.11 环境自检（`services/preflight.py` + `routes/system.py`）

- `GET /api/v1/system/check` 返回 `{ok, checks[]}`；每项含
  `key/label/ok/required/message/fix`。
- 检查项：Python 版本、后端依赖、asyncssh（可选）、adb 路径/版本/server/设备数
  （可选）、数据目录写权限。
- 启动时 `run_checks(deep=False)` 仅记录 warning，不阻塞启动；设置页用完整检查
  展示修复命令，设备页在 adb 缺失且无设备时给出告警。

---

## 4. 前端实现

### 4.1 骨架

- `main.tsx` → `router.tsx`：`createRootRoute` 内嵌 `QueryClientProvider` +
  antd `ConfigProvider(zh_CN, 主色 #1668dc)` + `AppLayout`（左侧竖排菜单）。
- 路由（代码式，无文件路由）：`/machines`、`/devices`、`/files`（设备选择）、
  `/devices/$uid/files`（文件浏览）、`/settings`，`/` 重定向 `/devices`。
- `vite.config.ts`：dev 端口 10012 绑 0.0.0.0，`/api` 代理到 `127.0.0.1:8010`；
  路径别名 `@ → src`。
- `package.json`：`react`/`react-dom` **精确版本 19.2.7 + overrides**——
  caret 曾把两者分别解析成 19.2.8/19.2.7，构建全绿但浏览器抛
  `Incompatible React versions` 白屏。

### 4.2 请求层（`api/request.ts`）

- axios 实例 `baseURL: /api/v1`，默认 60s 超时；上传、下载（含选中项打包）单独
  设置 `timeout: 0`，避免大文件被客户端提前中断。下载错误响应是 Blob，
  `extractErrorMessageAsync` 负责解析后展示。
- **token 引导**（`bootstrapApiToken` IIFE）：首屏若地址栏带 `?token=xxx`，
  写进 `localStorage` 后用 `history.replaceState` 把 token 从地址栏抹掉——
  token 留在 URL 里会被截图、转发、存进浏览器历史。
  axios 请求拦截器统一注入 `Authorization: Bearer <token>`。
- `extractErrorMessage`：错误文本从 `error.response.data.detail` 读
  （FastAPI 默认形态），兼容字符串 body / `message` 字段；读错层级会静默退化成
  axios 通用文案，掩盖真实原因（如「该路径需要 root 权限」）。
- `unwrap`：拆 `BaseResponse` 信封，`code !== 200` 抛 message。
- `isPermissionError`：正则 `/权限|permission denied|root|unauthorized/i`，
  文件页据此切换「尝试 root」引导 UI。

### 4.3 页面逻辑

- **ErrorBoundary（`components/ErrorBoundary.tsx`）**：包裹路由内容，
  单页渲染崩溃不再白屏——展示错误信息 + 刷新按钮。
- **DevicesPage**：`refetchInterval = sync_interval_seconds × 1000`（默认 3s，可配，
  R3）；首载与每 30 秒做一次全量同步（离线设备可自动恢复），其余轮询走轻量查询；
  支持按名称/型号/序列号/UID 过滤与排序；CPU/内存 `Progress` 圆环，离线/异常
  显示对应状态而不是「采样中…」；行级 loading 按当前 uid 区分；批量 root 逐台
  返回失败原因；设备名行内改名（`PATCH`）；行内「浏览文件」跳转。
- **FileBrowserPage**：
  - 状态三元组 `path / pathInput / searchMode+keyword`；点击导航与手输路径双向同步。
  - **路径与搜索词同步进 URL**（`validateSearch`）：分享链接直达同一目录、
    浏览器回退可用；URL 变化是唯一事实源，页面状态跟随。
  - `filesQuery` 的 queryKey 含 `uid+path+keyword+limit`，searchMode 时切换到 search 接口。
  - 设备详情按平台设置轮询驱动在线判断；**加载中 ≠ offline**（`deviceReady` 区分），
    避免 loading 窗口禁用控件掩盖真实原因。
  - 路径直达：`normalizePath` 归一化为绝对 POSIX 路径（未 root 设备必须能跳过
    不可读的父目录直达 `/data/local/tmp`）。
  - 面包屑：后端返回 `parts[{name,path}]`，逐级可点。
  - 越权：403 → `Alert` 展示后端引导文案，`isPermissionError` 时给出「尝试 root」。
  - 搜索范围可选「当前目录 / 全设备」；结果截断时「加载更多」逐步提高上限
    （200 → 1000）。
  - 上传支持多文件顺序上传、单文件进度/取消、覆盖确认（`timeout: 0`）；
    下载显示进度并可取消，超过 100MB 降级为浏览器直接下载。
  - 行内「更多」：复制、修改权限（chmod/chown）、文本/图片预览、打包下载；
    重命名支持目标目录（移动）。名称/时间/大小可排序；摘要显示条目数量。
  - **大目录虚拟滚动**：`Table virtual` + 固定列宽（名称 300，总 x≈1110，
    y=520），数千行目录只渲染视口。
    > ⚠️ 开 `virtual` 后数据行是 `<div class="ant-table-row">`，**不是 `<tr>`**。
    > 按标签名找行的自动化检查会静默失配（只匹配到表头一个 `tr`），
    > 必须按 `.ant-table-row` class 定位——`scripts/file-browser-check.mjs` 踩过这个坑。
  - **批量删除**：行多选 → 「删除选中 (N)」确认 → 限并发逐项 `/remove`
    （目录递归），失败项保留选中并逐项展示原因；换路径/搜索时清空选择。
  - **批量下载**：多选后调用 `/archive-selection` 打包 zip，显示进度并可取消。
- **MachinesPage**：机器编辑（保留设备记录、密钥/密码留空不修改）、SSH 密码
  状态展示、按行测试连接 loading、错误态、首屏 loading。
- **SettingsPage**：平台设置 + 环境自检卡片（状态/修复命令/重新检查）。

---

## 5. 性能设计汇总

| 手段 | 位置 | 效果 |
|---|---|---|
| 多机器并发同步（asyncio.gather，每机独立 session） | `devices.py::_sync_all_machines` | 请求不再等最慢/死机器 |
| 设备列表指标并发采样（gather，每任务独立 session） | `devices.py::list_devices` | N 台设备采样 = 最慢一台，不再相加 |
| root 探测 15s TTL 缓存（`set_root` 后失效） | `base.py::_root_cache` | 默认 3s 轮询不再每次打 `id` |
| SQLite WAL + busy_timeout 10s | `db/session.py::_enable_wal` | 并发任务写库不再 `database is locked` |
| MDD 缓冲读（8KB chunk + 实例级 buffer） | `mdd_client.py` | `recv(1)` 每字节一个 syscall → 协议级读；顺带修掉 `recv(256)` 吞后续数据 |
| 命令连接池（默认 4 条/机器，封顶不发散） | `remote_engine.py` | 不同设备命令并行，坏连接自愈；push/pull 走专用短连接不占池 |
| agent adb 并发上限（信号量，默认 8） | `agent/agent.py` | 多设备并行命令，突发不炸机 |
| 前端大目录虚拟滚动 | `FileBrowserPage` | 数千行只渲染视口 |
| agent FILE_PULL 分块流式（1MB，两遍读） | `agent/agent.py::_handle_pull` | 拉取内存 O(1)，不再整文件进 RAM |
| 远程 TCP 长连接常驻 | `remote_engine.py::_client` | 免每 3s 重新握手 |
| 指标缓存 2.5s + CPU 差分基线 | `metrics.py` | 轮询周期内一设备一次 shell |
| 一次 shell 读三段 /proc（marker 分段） | `base.py::METRICS_COMMAND` | 3 次往返 → 1 次 |
| 品牌与静态属性只读一次（进程内缓存） | `base.py::_brand_cache/_props_cache` | 轮询不再打 getprop |
| 故障熔断（短期跳过不可达机器） | `remote_engine.py::_failed_until` | 不每次等满超时 |
| 搜索并发 8 + 跳过伪文件系统 + 限额 | `file_service.py` | 全树搜索 6.2s → 0.6s |
| 阻塞 IO 全部 `asyncio.to_thread` | 两个 transport | 大文件不阻塞事件循环 |
| 列表接口同时返回指标 | `devices.py::list_devices` | 前端免逐台再拉 |

---

## 6. 安全设计汇总

| 威胁 | 对策 |
|---|---|
| 设备 shell 注入（`$(id)`、`` `cmd` `` 等） | 平台与 agent 两侧同一份 `FORBIDDEN_PATH_CHARS` 黑名单 + `shlex.quote`；serial 白名单正则 |
| agent 被当成通用 shell | 只放行 `adb ` 前缀命令，其它一律拒绝 |
| agent 裸奔 | 可选 `AUTH <token>` 首行认证（`hmac.compare_digest`），密钥按机器存 `engines.token` |
| 命令白名单逃逸（path 参数） | push/pull 的 `remote_path` 走 `_safe_device_path` 归一化 + 黑名单 |
| API 裸奔（局域网任何人可操作设备） | 可选 Bearer 鉴权：`API_AUTH_TOKEN` 配置后除 `/api/v1/health` 全部要求 `Authorization: Bearer <token>`（`hmac.compare_digest`）；下载锚点走 `?token=`；默认关闭不破坏演示流；中间件在 CORS **内侧**（preflight 无 Authorization 头） |
| 出问题难定位（无请求追踪） | `RequestIdMiddleware`：每请求盖 `X-Request-ID` 响应头（可传入透传）+ 访问日志 `rid=... method path -> status (ms)`，用户报障时凭 id 直接定位日志 |
| 同一机器重复登记刷脏数据 | `(kind, host, port)` 唯一索引 + API 400 + 启动自动去重 |
| 落地文件互相覆盖 | 唯一命名 + `STAGING_MAX_AGE_HOURS` 定期清理 |
| 大文件耗尽资源 | agent 收包上限 1GB；平台 `MAX_UPLOAD_BYTES` 2GiB 流式落盘；输出截断 512KB；FILE_PULL 分块 |
| 生产版本设备强行 root | 输出文本判定 + 明确 400 文案，不误报成功 |

---

## 7. 关键工程问题与解法（踩坑记录）

| 问题 | 现象 | 解法 |
|---|---|---|
| agent 信封污染输出 | `adb devices -l` 解析出「命令执行成功」假设备 | `unwrap_agent_output` 剥信封（正则 + 分节标签） |
| toybox `ls` 差异 | `-N` 不存在；`ls /sdcard` 只描述链接 | 固定 `ls -la <path>/` 补尾斜杠 |
| `getprop` 在复合命令中失效 | `adb shell "cat a; getprop x"` 拿不到值 | 品牌单独一次调用；属性用独立 getprop dump |
| `adb root` 返回码恒非 0 | 成功也报失败 | 以输出文本为准（`restarting adbd as root`）+ 等设备回归 |
| root 后设备短暂掉线 | 后续命令 timeout expired | `_wait_until_online`（echo online 轮询 ≤30s）/ agent 侧 `adb get-state` 轮询 |
| 父目录不可读卡死导航 | `/data` 403，永远到不了 `/data/local/tmp` | 路径直达 + 面包屑由请求路径构建 |
| SQLModel 不补列 | 模型加字段 → 老库 `no such column` | `_migrate_columns` PRAGMA 对比 + ALTER ADD COLUMN |
| aiosqlite 下 connect 事件拿到的不是 sqlite3.Connection | `isinstance(dbapi_connection, sqlite3.Connection)` 永远为 False，WAL pragma 静默跳过，`journal_mode` 仍是 delete | 放弃事件钩子，`init_db` 里用 `engine.connect()` + `isolation_level="AUTOCOMMIT"` 执行一次 `PRAGMA journal_mode=WAL`（WAL 是库级持久属性；且不能在事务内执行） |
| 并发任务共享 session | `commit() can't be called here` | 每机器同步任务独立 `async_session_maker` |
| react/react-dom 版本漂移 | 构建全绿但浏览器 `Incompatible React versions` 白屏 | 精确版本 + `overrides`；`render-check.sh` 真实浏览器断言 `#root` 有内容 |
| `adb connect` 误判 | USB serial 上该命令不适用，健康设备被标 offline | 失败只标 `error`，不标 offline |

---

## 8. 测试与验证体系

| 层 | 手段 | 说明 |
|---|---|---|
| 后端单测/集成 | `backend/tests/`（192 用例，28 个文件） | 覆盖：agent 协议与认证、信封解包、MDD 缓冲读、连接池并发、设备标识稳定性、文件操作、路径安全、root 切换、指标差分、迁移、鉴权、请求 ID、传输服务 |
| 假件隔离 | `tests/fake_mdd_server.py`、假 Transport、计数假引擎 | 真实设备不可达环境下的完整链路验证 |
| 后端 e2e | `scripts/e2e_demo.py`（TestClient）+ `live_server_check.py`（真 uvicorn + 真 HTTP） | 两级端到端 |
| **前端单测** | `npm test`（vitest + happy-dom，零额外 runtime 依赖） | format 工具、请求层错误提取、`?token=` 引导、ErrorBoundary 行为 |
| 前端门禁 | `tsc -b && vite build` | 类型 + 构建 |
| **真实浏览器渲染检查** | `scripts/render-check.sh`（headless chrome `--dump-dom`，零 npm 依赖） | 断言 `#root` 有实际内容——构建通过 ≠ 页面能渲染 |
| **真实交互检查** | `scripts/file-browser-check.mjs`（Chrome DevTools Protocol 直连） | 填路径 → 跳转 → 点面包屑 → 看到权限告警 |
| 带设备验收 | `scripts/render_check_with_device.py`、`real_device_check.py` | 需要一台 agent 机器 + 在线设备 |
| **CI** | `.github/workflows/ci.yml` | push/PR 双 job：后端 pytest + e2e；前端 vitest + tsc/build |
| 总门禁 | `./scripts/verify.sh` | 6 步串行，全过才算交付 |

---

## 9. 数据流示例（一次轮询）

```
1. 浏览器 3s 定时器 → GET /api/v1/devices（无 refresh）
2. 路由读 devices 表 + **并发**逐台 collector.collect(uid, transport, serial)（每任务独立 session，结果各自提交）
3. collector 命中 2.5s 缓存 → 直接返回样本；未命中 →
   transport.metrics(serial)
   ├─ local:  adb -s <serial> shell "cat /proc/stat; echo ---AFE---; ..."
   └─ remote: MDD 长连接 "adb -s <serial> shell ..." → agent 信封 → 解包
4. parse_metrics 拆三段 → CPU/Mem/Load；CPU% 对上次基线差分（首测 None）
5. brand/props/rooted 命中进程内缓存 → 不打设备
6. 组装 DeviceInfo（含 metrics）回写 DB → BaseResponse 信封 → axios unwrap → 表格渲染
```

## 10. 文件传输数据流（远程机器上传）

```
浏览器 multipart ──▶ FastAPI /upload
  → TransferService.stage_upload_stream（1MB 分块落盘 + 超上限即中断删半成品）
  → RemoteEngineTransport.push
     → 临时 MddClient 连接
     → FILE_PUSH:{filename,file_size,remote_path,md5,device_id}\n + 字节流
     → agent 收满 size（边收边算 md5，不符拒收）→ adb push <tmp> <device_path>
     → agent 回 OK / ERROR
  → TransferInfo{ok,path,size,stored_at} 信封返回
```
