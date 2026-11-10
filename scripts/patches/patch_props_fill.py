"""One-shot patcher: lazily collect the device static properties.

`_device_with_metrics` already persists the brand; the CPU architecture and
the Android version are the same kind of data - static, read once, cached by
the transport - so they follow the identical pattern.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROUTE = ROOT / "backend" / "app" / "api" / "routes" / "devices.py"
source = ROUTE.read_text(encoding="utf-8")

OLD = '''        # The brand is a device property, not a metric: persist it so the
        # device table shows it as well.
        if sample.brand and device.brand != sample.brand:
            device.brand = sample.brand
            device.updated_at = datetime.utcnow()
'''

NEW = '''        # The brand is a device property, not a metric: persist it so the
        # device table shows it as well.
        if sample.brand and device.brand != sample.brand:
            device.brand = sample.brand
            device.updated_at = datetime.utcnow()

        # The CPU architecture and the Android version are static device
        # properties too: read them once (the transport caches per device)
        # and persist, so the device table can show them.
        if device.cpu_abi is None or device.android_version is None:
            props_fetch = getattr(transport, "device_props", None)
            if props_fetch is not None:
                try:
                    props = await props_fetch(device.serial)
                except TransportError:
                    props = {}
                if props:
                    device.cpu_abi = props.get("ro.product.cpu.abi")
                    device.android_version = props.get("ro.build.version.release")
                    device.api_level = props.get("ro.build.version.sdk")
                    device.updated_at = datetime.utcnow()
'''

if OLD in source:
    source = source.replace(OLD, NEW, 1)
    ROUTE.write_text(source, encoding="utf-8")
    print("PROPS_FILL_OK")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)
