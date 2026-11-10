"""One-shot patcher: record the verified milestone status in PLAN.md.

Appends the verification evidence of this sprint to the milestone table of
``docs/PLAN.md`` so the plan and the delivered state stay in sync.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent.parent.parent / "docs" / "PLAN.md"

OLD_ROW = "| M6 端到端 | 假设备全链路 | 启动服务 → curl 全接口 → 贴输出 |"
NEW_ROW = """| M6 端到端 | 假设备全链路 | 启动服务 → curl 全接口 → 贴输出 |

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
6. 测试用 `asyncio.get_event_loop()` 在 pytest-asyncio 下会抛 RuntimeError → 改用 `asyncio.run()`。"""


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "### 验收结果" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_ROW not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(OLD_ROW, NEW_ROW, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
