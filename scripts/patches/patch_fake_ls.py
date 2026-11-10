"""One-shot patcher: fix the fake device directory listing.

``FakeDevice.ls`` decided "is this entry a directory?" with::

    if name in self.dirs.get(path, []):

``self.dirs[path]`` lists the CHILDREN of ``path``, not its own children names
that are directories, so the check never matched and every entry was rendered
as a directory row.

The patch renders a row from the real content type: an entry is a directory
when it is a key of ``self.dirs``.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "tests" / "fake_mdd_server.py"

OLD = '''        lines = ["total 8"]
        for name in names:
            if name in self.dirs.get(path, []):
                lines.append(f"drwxrwx--- 2 root u0_a12 4096 2024-01-01 12:00 {name}")
            else:
                content = self.files.get(f"{path.rstrip('/')}/{name}", b"")
                lines.append(
                    f"-rw-rw-rw- 1 root root {len(content):>6} 2024-01-02 13:45 {name}"
                )
        return "\\n".join(lines)
'''

NEW = '''        lines = ["total 8"]
        for name in names:
            child = f"{path.rstrip('/')}/{name}"
            if child in self.dirs:
                lines.append(f"drwxrwx--- 2 root u0_a12 4096 2024-01-01 12:00 {name}")
            else:
                content = self.files.get(child, b"")
                lines.append(
                    f"-rw-rw-rw- 1 root root {len(content):>6} 2024-01-02 13:45 {name}"
                )
        return "\\n".join(lines)
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "child = f\"{path.rstrip('/')}/{name}\"" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(OLD, NEW, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
