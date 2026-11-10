"""One-shot patcher: document the search optimisation and the root toggle.

The search was rewritten (parallel listings, pseudo file systems skipped) and
the file browser gained the "取消 root" button, so the README has to match.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent.parent.parent / "README.md"

REPLACEMENTS = [
    (
        "| **尝试 root** | 用 `adb root` 以 root 模式重启 adbd，之后受保护目录可读；已是 root 时按钮禁用 |",
        "| **root 提权** | 未 root 时显示「尝试 root」；已 root 时显示「取消 root」，两者可来回切换 |",
    ),
    (
        "| 关键字搜索 | 递归搜索文件名；受保护目录自动跳过 |",
        "| 关键字搜索 | 递归搜索文件名；受保护目录自动跳过；并发列举 + 跳过 `/proc` `/sys` 等伪文件系统，全树搜索 6.2s → 0.6s |",
    ),
    (
        """| 取消 root | `POST /api/v1/devices/{uid}/unroot` | `adb unroot` 恢复普通用户 |""",
        """| 取消 root | `POST /api/v1/devices/{uid}/unroot` | `adb unroot` 恢复普通用户；界面上的按钮会自动切换 |""",
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
            print(f"ANCHOR MISSING: {old[:70]!r}")
            return 1
    TARGET.write_text(source, encoding="utf-8")
    print(f"PATCHED ({applied} replacements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
