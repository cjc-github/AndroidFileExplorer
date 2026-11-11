"""One-shot patcher: collect CPU hardware facts (cores, max frequency, SoC).

Static hardware properties, collected once per device and cached:

* cores:       count `processor` lines of /proc/cpuinfo (works on ARM and x86)
* max freq:    max of /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq
               (kHz) - the glob is expanded by the DEVICE shell
* SoC/platform: getprop ro.board.platform (rides along with device_props)
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BE = ROOT / "backend"

edits = 0


def patch(path: pathlib.Path, pairs, label: str) -> None:
    global edits
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            edits += 1
            print(f"  {label}.{name} OK")
        elif new in source:
            print(f"  {label}.{name} already applied")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---------------------------------------------------------------- base.py
patch(
    BE / "app" / "transports" / "base.py",
    [
        (
            '''PROPERTY_KEYS = (
    "ro.product.cpu.abi",
    "ro.build.version.release",
    "ro.build.version.sdk",
)''',
            '''PROPERTY_KEYS = (
    "ro.product.cpu.abi",
    "ro.build.version.release",
    "ro.build.version.sdk",
    "ro.board.platform",
)''',
            "property keys",
        ),
        (
            '''    async def device_brand(self, serial: str) -> str | None:''',
            '''    def _cpu_info_cache(self) -> dict[str, tuple[int | None, int | None]]:
        """Cache of the CPU hardware facts per serial (they never change)."""
        cache = getattr(self, "_cpu_facts_cache", None)
        if cache is None:
            cache = {}
            self._cpu_facts_cache = cache
        return cache

    async def cpu_hardware_info(
        self, serial: str
    ) -> tuple[int | None, int | None]:
        """Return (core count, max frequency in kHz) of the device.

        Both are static hardware facts: /proc/cpuinfo counts the cores (the
        `processor` line exists on ARM and x86 alike) and the cpufreq sysfs
        holds the per-core maximum frequency in kHz - the glob is expanded by
        the DEVICE shell, so no quoting gymnastics are needed.  Cached per
        device; a missing value stays None and the UI renders a dash.
        """
        cache = self._cpu_info_cache()
        cached = cache.get(serial)
        if cached is not None:
            return cached

        cores: int | None = None
        max_freq: int | None = None
        try:
            cpuinfo = await self.shell(serial, "cat /proc/cpuinfo", timeout=10)
            cores = sum(
                1 for line in cpuinfo.splitlines() if line.lower().startswith("processor")
            ) or None
        except TransportError:
            cores = None

        try:
            freq_raw = await self.shell(
                serial,
                "cat /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq",
                timeout=10,
            )
            values = [
                int(line.strip())
                for line in freq_raw.splitlines()
                if line.strip().isdigit()
            ]
            max_freq = max(values) if values else None
        except (TransportError, ValueError):
            max_freq = None

        cache[serial] = (cores, max_freq)
        return cores, max_freq

    async def device_brand(self, serial: str) -> str | None:''',
            "cpu_hardware_info",
        ),
    ],
    "base",
)

# ---------------------------------------------------------------- model
patch(
    BE / "app" / "models" / "device.py",
    [
        (
            '''    cpu_abi: str | None = Field(default=None, max_length=100)''',
            '''    cpu_abi: str | None = Field(default=None, max_length=100)
    cpu_cores: int | None = Field(default=None)
    """Core count from /proc/cpuinfo."""
    cpu_max_freq_khz: int | None = Field(default=None)
    """Highest per-core maximum frequency in kHz (cpufreq sysfs)."""
    board_platform: str | None = Field(default=None, max_length=100)
    """SoC / board platform, from ro.board.platform."""''',
            "columns",
        ),
    ],
    "model",
)

# ---------------------------------------------------------------- schema
patch(
    BE / "app" / "schemas" / "device.py",
    [
        (
            '''    cpu_abi: str | None = None''',
            '''    cpu_abi: str | None = None
    cpu_cores: int | None = None
    cpu_max_freq_khz: int | None = None
    board_platform: str | None = None''',
            "fields",
        ),
    ],
    "schema",
)

# ---------------------------------------------------------------- route fill
patch(
    BE / "app" / "api" / "routes" / "devices.py",
    [
        (
            '''        if device.cpu_abi is None or device.android_version is None:
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
                    device.updated_at = datetime.utcnow()''',
            '''        if device.cpu_abi is None or device.android_version is None:
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
                    device.board_platform = props.get("ro.board.platform")
                    device.updated_at = datetime.utcnow()

        # The CPU hardware facts (core count, max frequency) are static too:
        # read them once (the transport caches per device) and persist.
        if device.cpu_cores is None or device.cpu_max_freq_khz is None:
            cpu_fetch = getattr(transport, "cpu_hardware_info", None)
            if cpu_fetch is not None:
                try:
                    cores, max_freq = await cpu_fetch(device.serial)
                except TransportError:
                    cores, max_freq = None, None
                if cores is not None:
                    device.cpu_cores = cores
                if max_freq is not None:
                    device.cpu_max_freq_khz = max_freq
                device.updated_at = datetime.utcnow()''',
            "route fill",
        ),
    ],
    "route",
)

print(f"DONE ({edits})")
