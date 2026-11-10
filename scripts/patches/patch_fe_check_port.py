"""One-shot patcher: make the frontend live check use a matching proxy target.

The Vite dev server proxies ``/api`` to ``http://127.0.0.1:8010`` (see
``frontend/vite.config.ts``), but the check started the backend on port 18021,
so every proxied call hit a closed port and returned 500.

The patch makes the check start the backend on the port the proxy expects
(8010) and keeps the alternative ports for the frontend only.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "scripts" / "frontend_live_check.py"


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "PROXY_TARGET_PORT" in source:
        print("ALREADY_PATCHED")
        return 0

    old = """BACKEND_PORT = 18021
FRONTEND_PORT = 18022
FRONTEND_BASE = f"http://127.0.0.1:{FRONTEND_PORT}"
"""
    new = """# The Vite dev server proxies /api to http://127.0.0.1:8010 (frontend/vite.config.ts),
# so the backend MUST listen on that port for the proxy chain to work.
PROXY_TARGET_PORT = 8010
BACKEND_PORT = PROXY_TARGET_PORT
FRONTEND_PORT = 18022
FRONTEND_BASE = f"http://127.0.0.1:{FRONTEND_PORT}"
"""
    if old not in source:
        print("ANCHOR_MISSING")
        return 1

    source = source.replace(old, new, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
