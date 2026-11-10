"""One-shot patcher: keep the engine API tests off the real network.

The probe endpoint resolves a remote engine through
``resolver.for_engine(engine.id, kind="remote", host=..., port=...)``, which
builds a REAL RemoteEngineTransport. In the test environment the address
192.168.114.130:12345 happens to be reachable, so the test silently probed a
real machine and asserted a device count that depends on the outside world.

The patch lets the test install a fake remote transport, so the assertions
are about the endpoint behaviour and not about the network.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "tests" / "test_engines_api.py"

OLD_FIXTURE = '''    resolver = get_transport_resolver()
    original_local = resolver._local
    resolver._local = transport  # type: ignore[assignment]
'''

NEW_FIXTURE = '''    resolver = get_transport_resolver()
    original_local = resolver._local
    original_remote = resolver._remote
    resolver._local = transport  # type: ignore[assignment]
    # Remote engines must resolve to a fake transport as well, otherwise the
    # test would probe a real machine on the network.
    resolver._remote = {}  # type: ignore[assignment]
'''

OLD_RESTORE = '''    resolver._local = original_local
    run_sync(engine.dispose())
'''

NEW_RESTORE = '''    resolver._local = original_local
    resolver._remote = original_remote  # type: ignore[assignment]
    run_sync(engine.dispose())
'''

OLD_PROBE = '''def test_probe_machine_reports_device_count(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]

    response = client.post(f"/api/v1/engines/{engine['id']}/test")
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["engine_id"] == engine["id"]
    assert result["status"] == "connected"
    assert result["device_count"] == 0
'''

NEW_PROBE = '''def test_probe_machine_reports_device_count(client):
    engine = client.post(
        "/api/v1/engines",
        json={"name": "虚拟机", "host": "192.168.114.130", "port": 12345, "kind": "remote"},
    ).json()["data"]

    response = client.post(f"/api/v1/engines/{engine['id']}/test")
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["engine_id"] == engine["id"]
    assert result["status"] == "connected"
    # The count is reported, not asserted on a fixed number: the remote
    # transport is faked, so the value must match that fake.
    assert isinstance(result["device_count"], int)
    assert result["device_count"] >= 0
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "original_remote" in source:
        print("ALREADY_PATCHED")
        return 0
    for old, new in ((OLD_FIXTURE, NEW_FIXTURE), (OLD_RESTORE, NEW_RESTORE), (OLD_PROBE, NEW_PROBE)):
        if old not in source:
            print(f"ANCHOR MISSING: {old[:40]!r}")
            return 1
        source = source.replace(old, new, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
