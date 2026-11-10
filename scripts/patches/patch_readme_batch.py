"""One-shot patcher: document the batch operation and the new test count."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TARGET = ROOT / "README.md"
source = TARGET.read_text(encoding="utf-8")

REPLACEMENTS = [
    (
        "| 设备表 | 设备名（**可点击改名**）、**品牌**",
        "| 设备表 | **多选批量操作**（批量尝试/取消 root）、设备名（**可点击改名**）、**品牌**",
    ),
    (
        "| 1 | 后端单元 + 集成测试（含 agent、设备标识、文件操作） | 88 passed |",
        "| 1 | 后端单元 + 集成测试（含 agent、设备标识、文件操作、root 切换） | 96 passed |",
    ),
]


def main() -> int:
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
