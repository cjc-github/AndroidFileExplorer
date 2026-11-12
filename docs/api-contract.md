# HTTP API 契约

> 基址：`/api/v1`。所有响应统一包装为 `{code, message, data, timestamp}`。
> `code=200` 表示业务成功；HTTP 状态码同时表达传输层语义。

## 错误码映射

| 场景 | HTTP | `detail` / `message` |
|---|---|---|
| 设备不存在 | 404 | `设备不存在` |
| 设备未授权（adb unauthorized） | 403 | `设备未授权，请先在设备上确认调试授权` |
| 路径需要 root / su | 403 | `Permission denied: /system` |
| 设备离线 | 400 | `设备当前状态为 offline` |
| 传输层故障（引擎不可达等） | 502 | 原始错误文本 |
| 参数校验失败 | 422 | FastAPI 默认校验信息 |
| 缺少 / 错误的 API token | 401 | `未授权：缺少或错误的 API token` |
| 同一台机器重复登记 | 400 | `该机器已登记: {host}:{port}（{kind}）` |

> **作用域约定**：路径级权限不足只影响该次请求（403），**不**改设备生命周期状态；
> 只有设备级操作（指标采集、adb 状态轮询）才会改写 `status`。

## 鉴权与请求追踪

**鉴权（可选）**：后端默认**不鉴权**（`API_AUTH_TOKEN` 留空）。
一旦配置了 `API_AUTH_TOKEN`，除 `GET /api/v1/health` 外的所有 `/api/v1` 请求
都必须带上 `Authorization: Bearer <token>`：

```http
Authorization: Bearer <token>
```

下载接口走浏览器锚点点击，无法设置请求头，因此允许用 query 参数代替：

```
GET /api/v1/devices/{uid}/files/download?path=/sdcard/a.bin&token=<token>
```

两者同时存在时**请求头优先**。未授权返回 **401**。

**请求 ID**：每个响应都带 `X-Request-ID` 头（请求自带则回显，否则生成 12 位十六进制 ID），
后端访问日志同步输出 `rid=<id> METHOD path -> status (耗时ms)`，
用户报错时凭这个 ID 可定位那一次请求的全部日志行。

**中间件顺序**（`app/main.py`）：CORS 最外层 → 请求 ID 居中 → 鉴权最内层。
浏览器 preflight（`OPTIONS`）不带 `Authorization` 头，所以 CORS 必须在鉴权之外；
鉴权应答 401 时不会调用更内层的栈，所以请求 ID 必须在鉴权**之外**才能盖到 401——
实测验证的结论（`CORS → 请求 ID → 鉴权` 是唯一同时满足 preflight 放行、
401 带 ID、401 带 CORS 头的顺序）。

---

## 1. 健康检查

### `GET /api/v1/health`

```json
{ "code": 200, "message": "操作成功", "data": { "status": "ok", "service": "android-file-explorer" } }
```

### `GET /api/v1/system/check`

环境自检。返回后端运行所需/可选依赖的状态，供设置页展示：

```json
{
  "code": 200,
  "data": {
    "ok": true,
    "checks": [
      {
        "key": "adb",
        "label": "本机 adb",
        "ok": true,
        "required": false,
        "message": "Android Debug Bridge version 1.0.41（/usr/bin/adb），adb server 正常，当前 1 台设备",
        "fix": null
      }
    ]
  }
}
```

`required=false` 的项（`adb`、`asyncssh`）只影响对应能力，缺失不会阻止后端启动；
`ok=false` 时 `fix` 给出可直接执行的修复提示。

---

## 2. 机器（挂载 ADB server 的宿主机）

对应界面：**远程机器连接配置** `/machines`。

### `GET /api/v1/engines`

返回所有机器。本机是 `kind=local`（`127.0.0.1:5037`）；远程机器是 `kind=remote`（agent）或 `kind=ssh`（SSH 直连）。`has_ssh_password` 只返回布尔值，不回显密码。

```json
{
  "code": 200,
  "data": [
    {
      "id": 1,
      "name": "本机",
      "host": "127.0.0.1",
      "port": 5037,
      "kind": "local",
      "status": "connected",
      "has_ssh_password": false,
      "last_error": null,
      "last_seen": "2026-11-10T01:15:59.074637",
      "created_at": "2026-11-10T01:15:59.074637",
      "updated_at": "2026-11-10T01:15:59.107384",
      "device_count": 0
    }
  ]
}
```

`device_count` 是该机器上已注册的设备数。

### `POST /api/v1/engines`

请求体：

```json
{ "name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote", "token": "<共享密钥，可选>" }
```

`token` 是该机器 agent 的共享密钥（`AUTH <token>` 握手），登记后所有发往该机器的
命令都会带上它；不填表示该 agent 未启用认证。

`(kind, host, port)` 上有唯一索引：**同一台机器只能登记一次**，重复登记返回 **400**：

```json
{ "detail": "该机器已登记: 192.168.114.130:12345（remote）" }
```

登记后立即探测该机器的 ADB server。探测失败**不会回滚登记**，`message` 说明原因，
`data.status` 变为 `error`。

### `PATCH /api/v1/engines/{engine_id}`

编辑机器配置，**保留该机器名下的设备记录**。请求体字段均可选：

```json
{
  "name": "新名字",
  "host": "192.168.114.130",
  "port": 12345,
  "token": "<新共享密钥，留空表示保留>",
  "ssh_username": "root",
  "ssh_password": "<新密码，留空表示保留>",
  "ssh_port": 22,
  "adb_path": "/usr/bin/adb"
}
```

保存后会清理旧 transport 缓存并按新配置重新探测。地址改成已登记的另一台机器返回 **400**。
`GET /api/v1/engines` 的 `has_ssh_password` 字段只返回布尔值，**不回显密码**。

### `POST /api/v1/engines/{engine_id}/test`

现场探测该机器，返回探测到的设备数。

```json
{
  "code": 200,
  "data": { "engine_id": 2, "status": "connected", "device_count": 1 },
  "message": "连接成功，探测到 1 台设备"
}
```

探测失败返回 **502**，且该机器的 `status` 置为 `error`、`last_error` 记录原因。

### `DELETE /api/v1/engines/{engine_id}`

删除机器**及其名下的设备记录**。本机（`kind=local`）不可删除，返回 **400**。

---

## 3. 设备

对应界面：**设备资源管理** `/devices`。

### `GET /api/v1/devices?refresh={true|false}`

- `refresh=true`：先从本机与所有远程机器同步一次设备清单，再返回。
- `refresh=false`（默认）：直接返回注册表中的已知设备。

```json
{
  "code": 200,
  "data": [
    {
      "uid": "95fad2083bf7161a",
      "serial": "emulator-5554",
      "engine_id": 1,
      "transport_kind": "local",
      "alias": null,
      "model": "Pixel_5",
      "brand": null,
      "product": null,
      "device": null,
      "platform": "android",
      "status": "online",
      "is_rooted": false,
      "last_error": null,
      "last_seen": "2026-11-10T01:15:59.074637",
      "created_at": "2026-11-10T01:15:59.074637",
      "updated_at": "2026-11-10T01:15:59.107384",
      "metrics": { "...": "见 metrics 结构" }
    }
  ]
}
```

### 设备唯一标识 `uid`

```
uid = sha1(f"{transport_kind}|{endpoint}|{serial}")[:16]
```

- `endpoint`：本机为 `local:127.0.0.1:5037`，远程为 `host:port`。
- 同一台设备重连后 `uid` **不变** —— 生命周期操作（连接/断开/状态）全部以它为键。
- 不同机器、不同序列号、不同传输方式绝不冲突。

### `GET /api/v1/devices/{uid}`

单台设备详情，附带一次实时指标采样。

### `GET /api/v1/devices/{uid}/metrics`

前台轮询入口（建议 3 秒一次）。

```json
{
  "code": 200,
  "data": {
    "cpu_percent": 23.5,
    "cpu_user_percent": 12.0,
    "cpu_system_percent": 11.5,
    "memory_total_bytes": 8589934592,
    "memory_available_bytes": 4294967296,
    "memory_used_bytes": 4294967296,
    "memory_percent": 50.0,
    "load_average": [0.5, 0.4, 0.3],
    "sampled_at": "2026-11-10T01:15:59.101866"
  }
}
```

> `cpu_percent` 为 `null` 表示**尚无基线**（首次采样），不是 0%。

### `POST /api/v1/devices/{uid}/connect`

重连该设备（生命周期操作）。成功后同步刷新注册表并重置 CPU 基线。

### `POST /api/v1/devices/{uid}/disconnect`

断开该设备，`data.status` 变为 `offline`，`last_error` 为 `已主动断开`。

### `PATCH /api/v1/devices/{uid}`

请求体 `{ "alias": "工位机 01" }` —— 改设备别名（留空则清空）。

### `POST /api/v1/devices/{uid}/root`

以 root 模式重启 adbd（`adb root`），成功后设备受保护目录可读。
生产版本设备会拒绝（`adbd cannot run as root in production builds`），此时返回 **400** 并说明原因。

### `POST /api/v1/devices/{uid}/unroot`

取消 root 模式（`adb unroot`）。未 root 的设备执行视为成功（幂等）。

### `POST /api/v1/devices/batch/root`

请求体 `{ "uids": ["uid1", "uid2"], "enable": true }` —— 批量切换 root 模式
（`enable=false` 表示批量取消 root），并发执行，逐台返回结果：

```json
{
  "code": 200,
  "data": [
    { "uid": "uid1", "ok": true, "message": "已切换 root 模式" },
    { "uid": "uid2", "ok": false, "message": "adbd cannot run as root in production builds" }
  ]
}
```

---

## 4. 设备文件

对应界面：**文件浏览** `/devices/{uid}/files`。

### `GET /api/v1/devices/{uid}/files?path={path}`

`path` 缺省为 `/`。

```json
{
  "code": 200,
  "data": {
    "current_path": "/sdcard",
    "parent_path": "/",
    "parts": [{ "name": "sdcard", "path": "/sdcard" }],
    "items": [
      {
        "name": "hello.txt",
        "path": "/sdcard/hello.txt",
        "is_dir": false,
        "size": 26,
        "modified_at": null,
        "permissions": "-rw-rw-rw-",
        "owner": "root",
        "group": "root",
        "is_link": false,
        "link_target": null
      }
    ],
    "truncated": false
  }
}
```

需要 root 的目录返回 **403**：

```json
{ "detail": "Permission denied: /system" }
```

### `GET /api/v1/devices/{uid}/files/search?keyword={kw}&path={path}&limit={n}`

按文件名递归搜索（深度与目录数有限制）。**受保护目录会被跳过，不影响整体搜索**。
`limit` 控制最多返回的结果数（默认 200，最大 1000）；达到上限时 `truncated=true`，
前端可提高 `limit` 继续加载。

### `GET /api/v1/devices/{uid}/files/preview?path={path}`

返回文本或图片的小文件预览（上限 **2MB**）：

```json
{
  "code": 200,
  "data": {
    "kind": "text",
    "content": "#!/system/bin/sh\n...",
    "size": 1254
  }
}
```

`kind` 为 `text` / `image` / `binary`；图片额外返回 `content_type`，`content` 为 base64。
目录、文件过大或无法读取大小时返回 **400**，文件不存在返回 **404**。

### `GET /api/v1/devices/{uid}/files/archive?path={path}`

把设备目录打包为 zip 并以附件回传。服务端递归列举后逐个拉取文件，
限制 **200 个文件 / 200MB / 6 层目录**，超出返回 **400**。
受保护目录和符号链接会被跳过，并在压缩包内 `_download_errors.txt` 中说明。

### `POST /api/v1/devices/{uid}/files/archive-selection`

请求体 `{ "paths": ["/sdcard/a.txt", "/sdcard/lib"] }` —— 把选中的文件 / 目录
（目录递归）打包为一个 zip 返回。规则与目录打包一致：最多 200 个文件 / 200MB，
受保护目录与符号链接会跳过并在 `_download_errors.txt` 中说明。

### `GET /api/v1/devices/{uid}/files/download?path={path}`

把设备文件拉下来**直接回传给浏览器**（`Content-Disposition: attachment`），
而不是只返回一个服务器路径让用户自己去捞。

链路：设备 → agent → 服务端 `DOWNLOAD_DIR` 暂存 → 以附件形式输出。
暂存文件带唯一名（`hello_a1b2c3d4e5f6.txt`），过期由 `STAGING_MAX_AGE_HOURS`（默认 24h）清理。

鉴权开启时，锚点下载无法设置请求头，用 `&token=<token>` 传参：

```
GET /api/v1/devices/{uid}/files/download?path=/sdcard/a.bin&token=<token>
```

文件不存在返回 **404**，设备离线返回 **400**，权限不足返回 **403**。

### `POST /api/v1/devices/{uid}/files/pull`

请求体 `{ "path": "/sdcard/hello.txt" }` —— 从设备拉取文件。

```json
{
  "code": 200,
  "data": {
    "ok": true,
    "path": "/sdcard/hello.txt",
    "size": 26,
    "stored_at": "/home/test/tcl/AndroidFileExplorer/data/downloads/hello_a1b2c3d4e5f6.txt",
    "message": ""
  }
}
```

> 落地文件带**唯一名**（原名 + 12 位随机后缀），不同设备的同名文件不会互相覆盖。
>
> 无论本机还是远程机器，拉取的字节流最终都落在 **API 服务器的下载暂存目录**：
> 远程链路是 设备 → agent（`adb pull` 到引擎机临时目录）→ `FILE_PULL` 字节流回传 →
> API 服务器落盘。`stored_at` 是**真实落地路径**，`GET /files/download` 端点
> 依赖它把文件回传给浏览器。

### `POST /api/v1/devices/{uid}/files/push`

请求体 `{ "path": "/sdcard/x.bin", "local_path": "/abs/path/x.bin" }` —— 上传到设备。

### `POST /api/v1/devices/{uid}/files/upload?device_path={path}`

`multipart/form-data`，字段名 `file`。服务端**分块流式落盘**到 `UPLOAD_DIR`
（不整体进内存，2GiB 上限超出即中止并删除半成品），推送完成后删除暂存副本。

### `POST /api/v1/devices/{uid}/files/mkdir`

请求体 `{ "path": "/sdcard/newdir" }` —— 新建目录（`mkdir -p`，自动建父目录）。

### `POST /api/v1/devices/{uid}/files/rename`

请求体 `{ "source": "/sdcard/a.txt", "target": "/sdcard/b.txt" }` —— 重命名 / 移动。
新名称与原名称相同时返回 **400**。

### `POST /api/v1/devices/{uid}/files/copy`

请求体 `{ "source": "/sdcard/a", "target": "/sdcard/b" }` —— 复制文件 / 目录
（设备侧 `cp -r`）。源和目标相同时返回 **400**。

### `POST /api/v1/devices/{uid}/files/permissions`

请求体：

```json
{ "path": "/sdcard/a", "mode": "644", "owner": "shell", "group": "shell" }
```

`mode` 为 3~4 位八进制；`owner` / `group` 可只填一项；至少填写一项。
格式非法返回 **400**，设备拒绝（常见于未 root 修改系统目录）返回 **403**。

### `POST /api/v1/devices/{uid}/files/remove`

请求体 `{ "path": "/sdcard/dir", "recursive": true }` —— 删除文件 / 目录
（目录递归删除）。路径级权限不足返回 **403**。

---

## 5. 平台设置

对应界面：**平台设置** `/settings`。

### `GET /api/v1/settings`

```json
{
  "code": 200,
  "data": {
    "sync_interval_seconds": 3,
    "auto_root": false,
    "auto_sync_seconds": 0
  }
}
```

### `PUT /api/v1/settings`

请求体与响应同结构。保存后**立即生效**，无需重启：

- `sync_interval_seconds`：设备页 / 文件选择页 / 文件浏览页的状态刷新间隔（1–60 秒）。
- `auto_sync_seconds`：后台自动同步间隔；0 = 后台不周期探测（页面打开期间仍会做恢复性同步，也可手动「同步」）。
- `auto_root`：每次同步后是否自动对未 root 的在线设备执行 `adb root`（每个同步周期只执行一次）。

---

## 6. 生命周期状态机

| 状态 | 含义 | 进入条件 |
|---|---|---|
| `online` | 设备在线且已授权 | `adb devices` 报告 `device` |
| `offline` | 设备掉线 | 从设备清单消失 / 主动断开 / adb 报 offline / 连续指标失败达到阈值 |
| `unauthorized` | 设备未授权调试 | `adb devices` 报告 `unauthorized` |
| `permission_denied` | 设备级权限不足 | 指标采集等设备级操作被拒 |
| `error` | 其他异常 | 未知 adb 状态 / 连续通用指标失败达到阈值 |

```
        ┌──────────── 心跳失败 / 从清单消失 ───────────┐
        ▼                                              │
   [offline] ◄── 主动 disconnect ── [online] ◄── adb=device
                    │                     │
                    │                     ├── adb=unauthorized ──► [unauthorized]
                    │                     └── 设备级权限被拒 ─────► [permission_denied]
```

路径级权限不足（`/system` 需要 su）**不进入** `permission_denied`，只返回 403。
