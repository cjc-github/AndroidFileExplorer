"""Process wide singletons (transport resolver, metrics collector)."""

from __future__ import annotations

from app.services.metrics import MetricsCollector
from app.services.transport_resolver import TransportResolver

_transport_resolver = TransportResolver()
_metrics_collector = MetricsCollector()


def get_transport_resolver() -> TransportResolver:
    """FastAPI dependency: the shared transport factory."""
    return _transport_resolver


def get_metrics_collector() -> MetricsCollector:
    """FastAPI dependency: the shared metrics collector."""
    return _metrics_collector


async def sync_known_engines() -> None:
    """Teach the resolver about every persisted machine."""
    from app.db.session import async_session_maker
    from app.models.device import EngineRecord
    from sqlalchemy import select

    async with async_session_maker() as session:
        result = await session.execute(select(EngineRecord))
        for engine in result.scalars().all():
            _transport_resolver.register_engine(
                engine.id,
                engine.kind,
                engine.host,
                engine.port,
                engine.token,
                engine.ssh_username,
                engine.ssh_password,
                engine.ssh_port,
                engine.adb_path,
            )
