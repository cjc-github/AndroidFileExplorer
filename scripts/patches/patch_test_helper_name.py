"""One-shot patcher: rename the module level helper to avoid a name clash.

The module level helper ``_run`` shadowed the inner ``async def _run`` of the
seed helpers, so ``_run(_run())`` resolved to the inner coroutine function.
Renaming the module level helper to ``run_sync`` removes the ambiguity.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
TARGETS = [ROOT / "tests" / "test_api.py", ROOT / "tests" / "test_permission_scope.py"]


def main() -> int:
    for target in TARGETS:
        source = target.read_text(encoding="utf-8")
        if "def run_sync(coro)" in source:
            print(f"{target.name}: already patched")
            continue

        source = source.replace("def _run(coro):", "def run_sync(coro):")
        source = source.replace("_run(_prepare())", "run_sync(_prepare())")
        source = source.replace("_run(engine.dispose())", "run_sync(engine.dispose())")
        source = source.replace("_run(_run())", "run_sync(_run())")
        target.write_text(source, encoding="utf-8")
        print(f"{target.name}: patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
