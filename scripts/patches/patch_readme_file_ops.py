"""One-shot patcher: document the file operations and the device alias.

The README was patched several times, so the anchors kept drifting.  This
patch works on LINE NUMBERS: it finds the line and inserts after it.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TARGET = ROOT / "README.md"

FILE_OPS_ROWS = [
    "| **新建目录** | 在当前目录下创建目录（`mkdir -p`） |",
    "| **重命名** | 行内操作，可改名或移动到同级的其它名字 |",
    "| **删除** | 行内操作，删除目录时递归删除，操作前二次确认 |",
]


def main() -> int:
    lines = TARGET.read_text(encoding="utf-8").splitlines()

    # 1. the file operation rows go after the keyword search row
    if any(row.startswith("| **新建目录**") for row in lines):
        print("ALREADY_PATCHED")
        return 0

    index = next(
        (i for i, line in enumerate(lines) if line.startswith("| 关键字搜索 |")),
        None,
    )
    if index is None:
        print("ANCHOR MISSING: | 关键字搜索 |")
        return 1
    lines[index + 1 : index + 1] = FILE_OPS_ROWS

    text = "\n".join(lines)

    # 2. the device table mentions the editable name
    text = text.replace(
        "| 设备表 | 设备名、**品牌**",
        "| 设备表 | 设备名（**可点击改名**）、**品牌**",
    )

    # 3. the test count
    text = text.replace(
        "| 1 | 后端单元 + 集成测试（含 agent 与设备标识） | 80 passed |",
        "| 1 | 后端单元 + 集成测试（含 agent、设备标识、文件操作） | 88 passed |",
    )

    TARGET.write_text(text, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
