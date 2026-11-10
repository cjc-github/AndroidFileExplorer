"""One-shot patcher: use Android/toybox compatible `ls` on real devices.

Two problems showed up only on a real device:

1. ``ls -lAN`` -> ``ls: Unknown option 'N'``: toybox (Android 8+) has no
   ``-N`` flag. Only ``-l`` and ``-a`` are safe.
2. ``ls -l /sdcard`` only printed the symlink itself, because toybox treats a
   symlink argument as "describe this link" instead of "list its content".
   A trailing slash forces the directory listing.

So the command becomes ``ls -la <path>/`` with a guaranteed trailing slash.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "base.py"

OLD_HELPER = '''def join_device_path(base: str, name: str) -> str:'''

NEW_HELPER = '''def directory_listing_path(path: str) -> str:
    """Return the path to hand to ``ls -l`` so that a directory is listed.

    Android's toybox ``ls`` describes a symlink argument instead of listing
    its content, so ``/sdcard`` would show only the link. A trailing slash
    forces the directory listing.
    """
    normalized = normalize_device_path(path)
    return normalized if normalized.endswith("/") else normalized + "/"


def join_device_path(base: str, name: str) -> str:'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "directory_listing_path" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_HELPER not in source:
        print("ANCHOR_BASE_MISSING")
        return 1
    source = source.replace(OLD_HELPER, NEW_HELPER, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED_BASE")

    # local_adb.py
    target = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "local_adb.py"
    text = target.read_text(encoding="utf-8")
    text = text.replace(
        '["-s", serial, "shell", "ls", "-lAN", normalized],',
        '["-s", serial, "shell", "ls", "-la", directory_listing_path(normalized)],',
    )
    text = text.replace(
        "    classify_output,\n    normalize_device_path,\n    parse_long_listing,\n)",
        "    classify_output,\n    directory_listing_path,\n    normalize_device_path,\n    parse_long_listing,\n)",
    )
    target.write_text(text, encoding="utf-8")
    print("PATCHED_LOCAL")

    # remote_engine.py
    target = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "remote_engine.py"
    text = target.read_text(encoding="utf-8")
    text = text.replace(
        'f"adb -s {shlex.quote(serial)} shell ls -lAN {shlex.quote(normalized)}",',
        'f"adb -s {shlex.quote(serial)} shell ls -la "\n                f"{shlex.quote(directory_listing_path(normalized))}",',
    )
    text = text.replace(
        "    classify_output,\n    normalize_device_path,\n    parse_long_listing,\n)",
        "    classify_output,\n    directory_listing_path,\n    normalize_device_path,\n    parse_long_listing,\n)",
    )
    target.write_text(text, encoding="utf-8")
    print("PATCHED_REMOTE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
