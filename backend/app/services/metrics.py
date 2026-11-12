"""CPU / memory sampling with a proper CPU usage delta.

``/proc/stat`` only exposes cumulative jiffy counters, so the usage percent is
computed from the delta between two consecutive samples. The first sample has
no baseline and therefore reports ``None`` instead of inventing a number.

Samples are cached for a short window: the foreground polls the device list
every few seconds and every device on every poll would otherwise open a new
shell on the device.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.transports.base import MetricsSample, Transport

#: How long a sample stays valid. The foreground polls every 3 seconds, so
#: this keeps the device traffic at roughly one sample per polling cycle.
DEFAULT_CACHE_TTL = 2.5


@dataclass(slots=True)
class _CpuBaseline:
    total: int
    idle: int
    user: int
    system: int


@dataclass(slots=True)
class _CachedSample:
    sample: MetricsSample
    taken_at: float


class MetricsCollector:
    """Keeps one CPU baseline and one cached sample per device."""

    def __init__(self, cache_ttl: float = DEFAULT_CACHE_TTL) -> None:
        self._baselines: dict[str, _CpuBaseline] = {}
        self._cache: dict[str, _CachedSample] = {}
        self._failures: dict[str, int] = {}
        self._cache_ttl = cache_ttl

    def reset(self, uid: str) -> None:
        """Forget the baseline of a device (e.g. after a reconnect)."""
        self._baselines.pop(uid, None)
        self._cache.pop(uid, None)
        self._failures.pop(uid, None)

    def record_failure(self, uid: str) -> int:
        """Count one failed sample and return the current streak.

        A single failed shell must not flip the UI to offline: transient
        errors happen while adbd restarts.  The routes use the streak to
        downgrade the device state only after repeated failures.
        """
        streak = self._failures.get(uid, 0) + 1
        self._failures[uid] = streak
        return streak

    def record_success(self, uid: str) -> None:
        """A successful sample ends the failure streak."""
        self._failures.pop(uid, None)

    def cached(self, uid: str) -> MetricsSample | None:
        """Return the cached sample of a device if it is still fresh."""
        entry = self._cache.get(uid)
        if entry is None:
            return None
        if time.monotonic() - entry.taken_at > self._cache_ttl:
            return None
        return entry.sample

    async def collect(
        self,
        uid: str,
        transport: Transport,
        serial: str,
        force: bool = False,
    ) -> MetricsSample:
        """Take one sample and compute the CPU usage against the previous one."""
        if not force:
            cached = self.cached(uid)
            if cached is not None:
                return cached

        sample = await transport.metrics(serial)
        self.record_success(uid)

        total = sample.raw_total_jiffies
        idle = sample.raw_idle_jiffies

        baseline = self._baselines.get(uid)
        if baseline is not None and total > baseline.total:
            delta_total = total - baseline.total
            delta_idle = max(0, idle - baseline.idle)
            delta_user = max(0, sample.raw_user_jiffies - baseline.user)
            delta_system = max(0, sample.raw_system_jiffies - baseline.system)

            sample.cpu_percent = round(
                max(0.0, min(100.0, (1 - delta_idle / delta_total) * 100)), 2
            )
            sample.cpu_user_percent = round(delta_user / delta_total * 100, 2)
            sample.cpu_system_percent = round(delta_system / delta_total * 100, 2)

        self._baselines[uid] = _CpuBaseline(
            total=total,
            idle=idle,
            user=sample.raw_user_jiffies,
            system=sample.raw_system_jiffies,
        )
        self._cache[uid] = _CachedSample(sample=sample, taken_at=time.monotonic())
        return sample
