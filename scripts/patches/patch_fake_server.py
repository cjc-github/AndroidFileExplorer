"""One-shot patcher: fix the fake MDD server request handler.

``socketserver.StreamRequestHandler`` creates ``self.rfile`` / ``self.wfile``
in ``setup()``. The handler derived from ``BaseRequestHandler`` never called
it, so every request died with ``AttributeError: '_Handler' object has no
attribute 'rfile'``.

The patch switches the base class to ``StreamRequestHandler`` and calls
``setup()`` explicitly.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "tests" / "fake_mdd_server.py"

OLD_IMPORT = "import socketserver\nimport threading\nfrom pathlib import Path\n"
NEW_IMPORT = "import socketserver\nimport threading\nfrom pathlib import Path\n"

OLD_CLASS = '''class _Handler(socketserver.BaseRequestHandler):
    """Handles one client connection."""

    def handle(self) -> None:
        device: FakeDevice = self.server.device  # type: ignore[attr-defined]
'''

NEW_CLASS = '''class _Handler(socketserver.StreamRequestHandler):
    """Handles one client connection."""

    def handle(self) -> None:
        # StreamRequestHandler.setup() creates self.rfile / self.wfile.
        self.setup()
        device: FakeDevice = self.server.device  # type: ignore[attr-defined]
'''

OLD_FINISH = '''        except Exception as exc:  # pragma: no cover - defensive
                self.wfile.write(f"ERROR:{exc}\\n".encode())
'''
NEW_FINISH = '''        except Exception as exc:  # pragma: no cover - defensive
                self.wfile.write(f"ERROR:{exc}\\n".encode())
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "StreamRequestHandler" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_CLASS not in source:
        print("ANCHOR_CLASS_MISSING")
        return 1
    source = source.replace(OLD_CLASS, NEW_CLASS, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
