"""One-shot patcher: use asyncio.run() instead of the deprecated global loop API.

``asyncio.get_event_loop().run_until_complete(...)`` raises inside a context
where an event loop is already running (pytest-asyncio installs one) and is
deprecated in 3.10+. ``asyncio.run`` creates and closes its own loop, which is
exactly what a synchronous fixture needs.

The patch rewrites both API test modules to use a small helper.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
TARGETS = [ROOT / "tests" / "test_api.py", ROOT / "tests" / "test_permission_scope.py"]

HELPER = '''

def _run(coro):
    """Run a coroutine from synchronous test code (own event loop)."""
    return asyncio.run(coro)
'''


def main() -> int:
    for target in TARGETS:
        source = target.read_text(encoding="utf-8")
        if "_run(coro)" in source:
            print(f"{target.name}: already patched")
            continue

        source = source.replace(
            "asyncio.get_event_loop().run_until_complete(_prepare())",
            "_run(_prepare())",
        )
        source = source.replace(
            "asyncio.get_event_loop().run_until_complete(_run())",
            "_run(_run())",
        )
        source = source.replace(
            "asyncio.get_event_loop().run_until_complete(engine.dispose())",
            "_run(engine.dispose())",
        )

        # Insert the helper right after the imports block.
        marker = "\n\n@pytest.fixture\ndef client("
        if marker not in source:
            print(f"{target.name}: anchor missing")
            continue
        source = source.replace(marker, HELPER + marker, 1)
        target.write_text(source, encoding="utf-8")
        print(f"{target.name}: patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
