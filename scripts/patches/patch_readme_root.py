"""One-shot patcher: document the root escalation and the new device table columns.

The README still described the old device table (no root flag, linear gauges)
and the old test count. This patch updates it to match the delivered state.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent.parent.parent / "README.md"

REPLACEMENTS = [
    (
        "| 设备表 | 设备名、**品牌**、序列号、**唯一标识 UID**、状态、**CPU**、**内存**、内存用量、最后在线、错误信息 |",
        "| 设备表 | 设备名、**品牌**、序列号、**唯一标识 UID**、状态、**CPU**（圆环）、**内存**（圆环 + 用量/总量）、**是否 root**、最后在线、错误信息 |",
    ),
    (
        "| 切换设备 | 文件页顶部「切换设备」返回选择页 |",
        "| 切换设备 | 文件页顶部「切换设备」返回选择页 |\n"
        "| **尝试 root** | 用 `adb root` 以 root 模式重启 adbd，之后受保护目录可读；已是 root 时按钮禁用 |",
    ),
    (
        "| 1 | 后端单元 + 集成测试 | 59 passed |",
        "| 1 | 后端单元 + 集成测试 | 68 passed |",
    ),
    (
        "## 权限（su）行为",
        """### root 提权

未 root 的设备访问不了 `/data`、`/system` 等目录。文件浏览页提供「尝试 root」按钮：

| 操作 | 接口 | 效果 |
|---|---|---|
| 尝试 root | `POST /api/v1/devices/{uid}/root` | `adb root` 重启 adbd，之后 shell 是 `uid=0(root)` |
| 取消 root | `POST /api/v1/devices/{uid}/unroot` | `adb unroot` 恢复普通用户 |

设备的 root 状态由 `id` 命令实时探测，**显示在设备表里**（金色 `root` 标签）。
生产版本设备会拒绝（`adbd cannot run as root in production builds`），此时接口返回 400 并说明原因。

### 权限（su）行为""",
    ),
]


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    applied = 0
    for old, new in REPLACEMENTS:
        if old in source:
            source = source.replace(old, new, 1)
            applied += 1
        elif new in source:
            print(f"already applied: {old[:40]!r}")
        else:
            print(f"ANCHOR MISSING: {old[:60]!r}")
            return 1
    TARGET.write_text(source, encoding="utf-8")
    print(f"PATCHED ({applied} replacements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
