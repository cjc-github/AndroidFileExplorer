"""One-shot patcher: discover devices automatically on first load.

The device list called ``getDeviceList(false)``, which only returns what the
registry already knows. On a fresh deployment the registry is empty, so the
user had to press the "同步" button before any device showed up - the page
looked broken instead of empty.

The patch makes the first query of a session sync the devices (``refresh=true``)
and every later poll use the cheap cached query.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" / "DeviceList.tsx"

OLD = '''  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: () => getDeviceList(false),
    refetchInterval: POLL_INTERVAL_MS,
  });
'''

NEW = '''  // The first load syncs the machines so the user does not have to press
  // "同步" manually; later polls use the cheap cached query.
  const hasSyncedRef = useRef(false);

  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: () => {
      if (!hasSyncedRef.current) {
        hasSyncedRef.current = true;
        return getDeviceList(true);
      }
      return getDeviceList(false);
    },
    refetchInterval: POLL_INTERVAL_MS,
  });
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "hasSyncedRef" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD not in source:
        print("ANCHOR_MISSING")
        return 1
    source = source.replace(OLD, NEW, 1)
    source = source.replace(
        'import { useState } from "react";',
        'import { useRef, useState } from "react";',
        1,
    )
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
