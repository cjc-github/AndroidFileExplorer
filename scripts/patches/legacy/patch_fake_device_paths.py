"""One-shot patcher: the fake device must accept trailing-slash paths.

The real toybox needs a trailing slash to list a symlinked directory, so
the transport now sends ``ls -la /sdcard/``. The fake device keyed its tree
by ``/sdcard`` and answered "No such file or directory".

The patch normalizes the requested path (strip a single trailing slash)
before looking it up, exactly like a real file system does.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "tests" / "fake_mdd_server.py"

OLD_LS = '''    def ls(self, path: str) -> str:
        if path in ("/data", "/system"):
            return f"ls: {path}: Permission denied"
        names = self.dirs.get(path)
'''

NEW_LS = '''    def ls(self, path: str) -> str:
        # A real file system ignores a trailing slash; the transport adds one
        # so that toybox lists a symlinked directory instead of describing it.
        normalized = path.rstrip("/") or "/"
        if normalized in ("/data", "/system"):
            return f"ls: {path}: Permission denied"
        path = normalized
        names = self.dirs.get(path)
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "normalized = path.rstrip" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_LS not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(OLD_LS, NEW_LS, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
