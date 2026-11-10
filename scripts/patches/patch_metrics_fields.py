"""One-shot patcher: promote the private _raw_* CPU counters to real fields.

The MetricsSample dataclass uses slots=True, so assigning ``sample._raw_total``
would raise AttributeError at runtime. This script rewrites base.py so the
counters become declared fields and the metrics service can rely on them.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "base.py"

OLD_FIELDS = """    load_average: tuple[float, float, float] | None = None
    uptime_seconds: float | None = None
    sampled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
"""

NEW_FIELDS = '''    load_average: tuple[float, float, float] | None = None
    uptime_seconds: float | None = None
    sampled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    raw_total_jiffies: int = 0
    """Raw cumulative CPU counter of /proc/stat (for delta computation)."""

    raw_idle_jiffies: int = 0
    """Raw cumulative idle counter of /proc/stat (for delta computation)."""

    raw_user_jiffies: int = 0
    """Raw cumulative user counter of /proc/stat."""

    raw_system_jiffies: int = 0
    """Raw cumulative system counter of /proc/stat."""
'''

OLD_PARSE = """    total = idle = 0
    if cpu_line:
        fields = cpu_line.split()[1:]
        try:
            numbers = [int(value) for value in fields]
        except ValueError:
            numbers = []
        if numbers:
            total = sum(numbers)
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)

    sample._raw_total = total  # type: ignore[attr-defined]
    sample._raw_idle = idle  # type: ignore[attr-defined]
"""

NEW_PARSE = """    total = idle = user = system = 0
    if cpu_line:
        fields = cpu_line.split()[1:]
        try:
            numbers = [int(value) for value in fields]
        except ValueError:
            numbers = []
        if numbers:
            total = sum(numbers)
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
            user = numbers[0] + numbers[1]
            system = numbers[2]
            if len(numbers) > 5:
                system += numbers[5]  # irq
            if len(numbers) > 6:
                system += numbers[6]  # softirq
            if len(numbers) > 7:
                system += numbers[7]  # steal

    sample.raw_total_jiffies = total
    sample.raw_idle_jiffies = idle
    sample.raw_user_jiffies = user
    sample.raw_system_jiffies = system
"""


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "raw_total_jiffies" in source and "_raw_total" not in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_FIELDS not in source:
        print("ANCHOR_FIELDS_MISSING")
        return 1
    if OLD_PARSE not in source:
        print("ANCHOR_PARSE_MISSING")
        return 1
    source = source.replace(OLD_FIELDS, NEW_FIELDS, 1)
    source = source.replace(OLD_PARSE, NEW_PARSE, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
