"""One-shot patcher: let the transport resolver be overridden in tests.

The probe endpoint resolves a remote engine through
``resolver.for_engine(engine.id, kind="remote", host=..., port=...)``, which
builds a REAL RemoteEngineTransport. In the test environment the address
192.168.114.130:12345 happens to be reachable, so the test silently probed a
real machine and asserted a device count that depends on the outside world.

The patch adds an explicit override hook to the resolver: when a factory is
installed, every remote machine is resolved through it. Tests install a fake
factory, production keeps the real one.
"""

import pathlib

RESOLVER = pathlib.Path(__file__).resolve().parent / "app" / "services" / "transport_resolver.py"
TEST = pathlib.Path(__file__).resolve().parent / "tests" / "test_engines_api.py"

OLD_INIT = '''        self._engines: dict[int, tuple[str, str, int]] = {}
        """engine_id -> (kind, host, port)."""
'''

NEW_INIT = '''        self._engines: dict[int, tuple[str, str, int]] = {}
        """engine_id -> (kind, host, port)."""
        self._remote_override: "Callable[[str, int], Transport] | None" = None
        """Test hook: replaces the construction of remote transports."""
'''

OLD_FOR = '''        key = (host, port)
        transport = self._remote.get(key)
        if transport is None:
'''

NEW_FOR = '''        if self._remote_override is not None:
            return self._remote_override(host, port)

        key = (host, port)
        transport = self._remote.get(key)
        if transport is None:
'''


def patch_resolver() -> bool:
    source = RESOLVER.read_text(encoding="utf-8")
    if "_remote_override" in source:
        print("resolver already patched")
        return True
    if OLD_INIT not in source:
        print("ANCHOR_INIT_MISSING")
        return False
    if OLD_FOR not in source:
        print("ANCHOR_FOR_MISSING")
        return False
    source = source.replace(OLD_INIT, NEW_INIT, 1)
    source = source.replace(OLD_FOR, NEW_FOR, 1)
    source = source.replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\n\nfrom collections.abc import Callable\n",
        1,
    )
    RESOLVER.write_text(source, encoding="utf-8")
    print("resolver patched")
    return True


def patch_test() -> bool:
    source = TEST.read_text(encoding="utf-8")
    if "_remote_override" in source:
        print("test already patched")
        return True

    old = '''    resolver = get_transport_resolver()
    original_local = resolver._local
    resolver._local = transport  # type: ignore[assignment]
'''
    new = '''    resolver = get_transport_resolver()
    original_local = resolver._local
    original_override = resolver._remote_override
    resolver._local = transport  # type: ignore[assignment]
    # Remote engines must resolve to a fake transport as well, otherwise the
    # test would probe a real machine on the network.
    resolver._remote_override = lambda _host, _port: transport
'''
    if old not in source:
        print("ANCHOR_FIXTURE_MISSING")
        return False
    source = source.replace(old, new, 1)

    old_restore = '''    resolver._local = original_local
    run_sync(engine.dispose())
'''
    new_restore = '''    resolver._local = original_local
    resolver._remote_override = original_override
    run_sync(engine.dispose())
'''
    if old_restore not in source:
        print("ANCHOR_RESTORE_MISSING")
        return False
    source = source.replace(old_restore, new_restore, 1)

    old_probe = '''    assert result["device_count"] == 0
'''
    new_probe = '''    # The fake transport reports no devices; the point is that the probe
    # used the fake and not a real machine on the network.
    assert result["device_count"] == 0
'''
    source = source.replace(old_probe, new_probe, 1)

    TEST.write_text(source, encoding="utf-8")
    print("test patched")
    return True


def main() -> int:
    if not patch_resolver():
        return 1
    if not patch_test():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
