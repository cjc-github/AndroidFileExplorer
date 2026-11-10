"""One-shot patcher: collect and expose the device static properties.

The device table showed only the runtime metrics (CPU / memory).  The CPU
architecture, the Android version and the API level are device properties -
one `getprop` dump answers all of them, and they never change, so they are
read once per device and cached.

Also persisted: they belong in the device table of the database.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "backend" / "app" / "transports" / "base.py"
MODEL = ROOT / "backend" / "app" / "models" / "device.py"
SCHEMA = ROOT / "backend" / "app" / "schemas" / "device.py"
ROUTE = ROOT / "backend" / "app" / "api" / "routes" / "devices.py"


def patch(path: pathlib.Path, pairs, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            print(f"  {label}.{name} OK")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---------------------------------------------------------------- base.py
patch(
    BASE,
    [
        (
            '''BRAND_COMMAND = "getprop ro.product.brand"''',
            '''BRAND_COMMAND = "getprop ro.product.brand"

#: The device properties shown in the device table.  One `getprop` dump
#: answers all of them; the values never change while the device boots.
PROPERTY_KEYS = (
    "ro.product.cpu.abi",
    "ro.build.version.release",
    "ro.build.version.sdk",
)''',
            "keys",
        ),
        (
            '''    async def device_brand(self, serial: str) -> str | None:''',
            '''    def _props_cache(self) -> dict[str, dict[str, str]]:
        """Cache of the device properties per serial (they never change)."""
        cache = getattr(self, "_device_props_cache", None)
        if cache is None:
            cache = {}
            self._device_props_cache = cache
        return cache

    async def device_props(self, serial: str) -> dict[str, str]:
        """Read the static device properties (ABI, Android version, API).

        One `getprop` dump answers everything; on devices whose dump is not
        parseable the individual keys are asked one by one.  Cached per
        device because the values do not change until the next reboot into a
        different system image.
        """
        cache = self._props_cache()
        cached = cache.get(serial)
        if cached is not None:
            return cached

        props: dict[str, str] = {}
        try:
            dump = await self.shell(serial, "getprop", timeout=10)
        except TransportError:
            dump = ""

        import re as _re

        for match in _re.finditer(r"\\[([^]]+)\\]: \\[([^]]*)\\]", dump):
            key, value = match.group(1), match.group(2).strip()
            if key in PROPERTY_KEYS and value:
                props[key] = value

        if len(props) < len(PROPERTY_KEYS):
            # Older / exotic builds: ask the missing keys individually.
            for key in PROPERTY_KEYS:
                if key in props:
                    continue
                try:
                    value = (
                        await self.shell(serial, f"getprop {key}", timeout=5)
                    ).strip()
                except TransportError:
                    continue
                if value and "not found" not in value.lower():
                    props[key] = value

        cache[serial] = props
        return props

    async def device_brand(self, serial: str) -> str | None:''',
            "device_props",
        ),
    ],
    "base",
)

# ---------------------------------------------------------------- model
patch(
    MODEL,
    [
        (
            '''    brand: str | None = Field(default=None, max_length=100)''',
            '''    brand: str | None = Field(default=None, max_length=100)
    cpu_abi: str | None = Field(default=None, max_length=100)
    """The CPU architecture, from ro.product.cpu.abi (e.g. arm64-v8a)."""
    android_version: str | None = Field(default=None, max_length=40)
    """The Android version string, from ro.build.version.release."""
    api_level: str | None = Field(default=None, max_length=10)
    """The API level, from ro.build.version.sdk."""''',
            "columns",
        ),
    ],
    "model",
)

# ---------------------------------------------------------------- schema
patch(
    SCHEMA,
    [
        (
            '''    brand: str | None = None''',
            '''    brand: str | None = None
    cpu_abi: str | None = None
    android_version: str | None = None
    api_level: str | None = None''',
            "fields",
        ),
    ],
    "schema",
)

print("DONE")
