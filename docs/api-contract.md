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

> **作用域约定**：路径级权限不足只影响该次请求（403），**不**改设备生命周期状态；
> 只有设备级操作（指标采集、adb 状态轮询）才会改写 `status`。

---

## 1. 健康检查

### `GET /api/v1/health`

```json
{ "code": 200, "message": "操作成功", "data": { "status": "ok", "service": "android-file-explorer" } }
```

---

## 2. 机器（挂载 ADB server 的宿主机）

对应界面：**远程机器连接配置** `/machines`。

### `GET /api/v1/engines`

返回所有机器。本机是 `kind=local`（`127.0.0.1:5037`），远程引擎是 `kind=remote`。

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
{ "name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote" }
```

登记后立即探测该机器的 ADB server。探测失败**不会回滚登记**，`message` 说明原因，
`data.status` 变为 `error`。

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

### `GET /api/v1/devices/{uid}/files/search?keyword={kw}&path={path}`

按文件名递归搜索（深度与结果数有限制）。**受保护目录会被跳过，不影响整体搜索**。

### `POST /api/v1/devices/{uid}/files/pull`

请求体 `{ "path": "/sdcard/hello.txt" }` —— 从设备拉取文件。

```json
{
  "code": 200,
  "data": {
    "ok": true,
    "path": "/sdcard/hello.txt",
    "size": 26,
    "stored_at": "/home/test/tcl/AndroidFileExplorer/data/downloads/hello.txt",
    "message": ""
  }
}
```

> 远程机器的拉取落在**那台远程机器**上，`stored_at` 会写成 `host:port:/path`，不谎称落在 API 服务器。

### `POST /api/v1/devices/{uid}/files/push`

请求体 `{ "path": "/sdcard/x.bin", "local_path": "/abs/path/x.bin" }` —— 上传到设备。

### `POST /api/v1/devices/{uid}/files/upload?device_path={path}`

`multipart/form-data`，字段名 `file`。服务端先落到 `UPLOAD_DIR` 再推送。

---

## 5. 生命周期状态机

| 状态 | 含义 | 进入条件 |
|---|---|---|
| `online` | 设备在线且已授权 | `adb devices` 报告 `device` |
| `offline` | 设备掉线 | 从设备清单消失 / 主动断开 / adb 报 offline |
| `unauthorized` | 设备未授权调试 | `adb devices` 报告 `unauthorized` |
| `permission_denied` | 设备级权限不足 | 指标采集等设备级操作被拒 |
| `error` | 其他异常 | 未知 adb 状态 |

```
        ┌──────────── 心跳失败 / 从清单消失 ───────────┐
        ▼                                              │
   [offline] ◄── 主动 disconnect ── [online] ◄── adb=device
                    │                     │
                    │                     ├── adb=unauthorized ──► [unauthorized]
                    │                     └── 设备级权限被拒 ─────► [permission_denied]
```

路径级权限不足（`/system` 需要 su）**不进入** `permission_denied`，只返回 403。
