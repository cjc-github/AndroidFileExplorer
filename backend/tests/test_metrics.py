"""Metrics collection: CPU delta, memory parsing and the sample cache."""

from __future__ import annotations

import pytest

from app.services.metrics import MetricsCollector
from app.transports.base import MetricsSample


class ScriptedTransport:
    """Transport that returns a scripted sequence of samples."""

    kind = "fake"

    def __init__(self, samples: list[MetricsSample]) -> None:
        self.samples = samples
        self.index = 0

    @property
    def endpoint(self) -> str:
        return "scripted:1"

    async def metrics(self, serial: str) -> MetricsSample:
        sample = self.samples[min(self.index, len(self.samples) - 1)]
        self.index += 1
        return sample


def make_sample(total: int, idle: int, user: int = 10, system: int = 5) -> MetricsSample:
    sample = MetricsSample()
    sample.raw_total_jiffies = total
    sample.raw_idle_jiffies = idle
    sample.raw_user_jiffies = user
    sample.raw_system_jiffies = system
    sample.memory_total_bytes = 1000
    sample.memory_available_bytes = 400
    sample.memory_used_bytes = 600
    sample.memory_percent = 60.0
    return sample


@pytest.mark.asyncio
async def test_first_sample_has_no_cpu_baseline():
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60)])
    sample = await collector.collect("uid-1", transport, "serial")
    assert sample.cpu_percent is None, "no baseline -> no invented number"
    assert sample.memory_percent == 60.0


@pytest.mark.asyncio
async def test_cpu_percent_is_computed_from_the_delta():
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60), make_sample(200, 100, 40, 20)])
    await collector.collect("uid-1", transport, "serial")
    sample = await collector.collect("uid-1", transport, "serial", force=True)

    # delta_total = 100, delta_idle = 40 -> 1 - 0.4 = 60%
    assert sample.cpu_percent == 60.0
    assert sample.cpu_user_percent == 30.0
    assert sample.cpu_system_percent == 15.0


@pytest.mark.asyncio
async def test_baselines_are_kept_per_device():
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60), make_sample(200, 100, 40, 20)])
    await collector.collect("uid-1", transport, "serial")
    sample = await collector.collect("uid-2", transport, "serial", force=True)
    assert sample.cpu_percent is None, "a different device has no baseline yet"


@pytest.mark.asyncio
async def test_reset_clears_the_baseline():
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60), make_sample(200, 100, 40, 20)])
    await collector.collect("uid-1", transport, "serial")
    collector.reset("uid-1")
    sample = await collector.collect("uid-1", transport, "serial", force=True)
    assert sample.cpu_percent is None


@pytest.mark.asyncio
async def test_a_fresh_sample_is_served_from_the_cache():
    """The polling loop must not hit the device again for every tick."""
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60), make_sample(200, 100, 40, 20)])
    first = await collector.collect("uid-1", transport, "serial")
    assert transport.index == 1

    cached = await collector.collect("uid-1", transport, "serial")
    assert transport.index == 1, "the device must not be queried again"
    assert cached is first


@pytest.mark.asyncio
async def test_force_bypasses_the_cache():
    collector = MetricsCollector()
    transport = ScriptedTransport([make_sample(100, 60), make_sample(200, 100, 40, 20)])
    await collector.collect("uid-1", transport, "serial")
    await collector.collect("uid-1", transport, "serial", force=True)
    assert transport.index == 2
