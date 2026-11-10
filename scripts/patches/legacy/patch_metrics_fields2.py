"""One-shot patcher: collect load average and brand with the metric sample.

The device list showed ``load_average = None`` and ``brand = None`` even
though the device provides both:

    /proc/loadavg        -> 0.00 0.02 0.13 1/1284 6607
    ro.product.brand     -> google

The patch extends the single shell round trip so the sample carries them:

    cat /proc/stat; echo MARKER; cat /proc/meminfo; echo MARKER;
    cat /proc/loadavg; echo MARKER; getprop ro.product.brand
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "base.py"

OLD_MARKER = '''#: Marker used by the fake/real engines to separate procfs payloads.
PROCFS_MARKER = "---AFE---"
'''

NEW_MARKER = '''#: Marker used by the fake/real engines to separate procfs payloads.
PROCFS_MARKER = "---AFE---"
#: Second marker: the device property section follows the procfs payloads.
PROPS_MARKER = "---AFEPROPS---"

#: The single shell round trip that collects every metric we report.
METRICS_COMMAND = (
    "cat /proc/stat; "
    f"echo '{PROCFS_MARKER}'; "
    "cat /proc/meminfo; "
    f"echo '{PROPS_MARKER}'; "
    "cat /proc/loadavg; "
    "echo '---AFEBRAND---'; "
    "getprop ro.product.brand"
)
'''

OLD_METRICS = '''    async def metrics(self, serial: str) -> MetricsSample:
        """Collect one CPU/memory sample of a device."""
        raw = await self.shell(
            serial,
            f"cat /proc/stat; echo '{PROCFS_MARKER}'; cat /proc/meminfo",
        )
        return parse_metrics(raw)
'''

NEW_METRICS = '''    async def metrics(self, serial: str) -> MetricsSample:
        """Collect one CPU / memory / load sample of a device."""
        raw = await self.shell(serial, METRICS_COMMAND)
        return parse_metrics(raw)
'''

OLD_PARSE = '''    stat_part, _, mem_part = raw.partition(PROCFS_MARKER)
'''

NEW_PARSE = '''    stat_part, _, remainder = raw.partition(PROCFS_MARKER)
    mem_part, _, props_part = remainder.partition(PROPS_MARKER)
'''

OLD_TAIL = '''    if mem_total is not None:
        sample.memory_total_bytes = mem_total
        if mem_available is not None:
            sample.memory_available_bytes = mem_available
            sample.memory_used_bytes = max(0, mem_total - mem_available)
            sample.memory_percent = round(sample.memory_used_bytes / mem_total * 100, 2)
    return sample
'''

NEW_TAIL = '''    if mem_total is not None:
        sample.memory_total_bytes = mem_total
        if mem_available is not None:
            sample.memory_available_bytes = mem_available
            sample.memory_used_bytes = max(0, mem_total - mem_available)
            sample.memory_percent = round(sample.memory_used_bytes / mem_total * 100, 2)

    # /proc/loadavg -> "0.00 0.02 0.13 1/1284 6607"
    load_part, _, brand_part = props_part.partition("---AFEBRAND---")
    load_values = load_part.strip().split()
    if len(load_values) >= 3:
        try:
            sample.load_average = (
                float(load_values[0]),
                float(load_values[1]),
                float(load_values[2]),
            )
        except ValueError:
            pass
    sample.brand = brand_part.strip() or None
    return sample
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "PROPS_MARKER" in source:
        print("ALREADY_PATCHED")
        return 0

    for old, new in (
        (OLD_MARKER, NEW_MARKER),
        (OLD_METRICS, NEW_METRICS),
        (OLD_PARSE, NEW_PARSE),
        (OLD_TAIL, NEW_TAIL),
    ):
        if old not in source:
            print(f"ANCHOR MISSING: {old[:50]!r}")
            return 1
        source = source.replace(old, new, 1)

    # The sample needs a brand field.
    old_field = """    raw_system_jiffies: int = 0
    \"\"\"Raw cumulative system counter of /proc/stat.\"\"\"
"""
    new_field = """    raw_system_jiffies: int = 0
    \"\"\"Raw cumulative system counter of /proc/stat.\"\"\"

    brand: str | None = None
    \"\"\"ro.product.brand of the device, when the device reports one.\"\"\"
"""
    if old_field not in source:
        print("ANCHOR FIELD MISSING")
        return 1
    source = source.replace(old_field, new_field, 1)

    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
