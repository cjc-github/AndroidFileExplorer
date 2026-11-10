"""One-shot patcher: record the device property columns in the checklist."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
FEATURES = ROOT / "docs" / "FEATURES.md"
source = FEATURES.read_text(encoding="utf-8")

OLD = """- [x] 设备别名（alias）：设备名可点击改名，列表即显
- [x] 是否 root 实时探测（`id` → `uid=0`），金色标签展示"""
NEW = """- [x] 设备别名（alias）：设备名可点击改名，列表即显
- [x] 是否 root 实时探测（`id` → `uid=0`），表格独立 Root 列（是/否）
- [x] 设备静态属性入表：CPU 架构（`ro.product.cpu.abi`）、Android 版本 + API level（一次 `getprop` 全量采集，惰性缓存，落库）
- [x] 轮询路径的设备属性变更真正落库（修复 `list_devices` 从不 commit 的缺陷）"""

if OLD in source:
    source = source.replace(OLD, NEW, 1)
    FEATURES.write_text(source, encoding="utf-8")
    print("FEATURES_OK")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)

README = ROOT / "README.md"
readme = README.read_text(encoding="utf-8")
old_row = "| 设备表 | **多选批量操作**（批量尝试/取消 root）、设备名（**可点击改名**）、**品牌**、序列号、**唯一标识 UID**、状态、**CPU**（圆环）、**内存**（圆环 + 用量/总量）、**是否 root**、最后在线、错误信息 |"
new_row = "| 设备表 | **多选批量操作**（批量尝试/取消 root）、设备名（**可点击改名**）、**品牌**、序列号、**唯一标识 UID**、状态、**Root**（是/否）、**系统**（Android 版本 + API level + CPU 架构）、**CPU**（圆环）、**内存**（圆环 + 用量/总量）、最后在线、错误信息 |"
if old_row in readme:
    README.write_text(readme.replace(old_row, new_row, 1), encoding="utf-8")
    print("README_OK")
else:
    print("README row drifted - check manually")
