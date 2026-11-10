"""Regression test for the lightweight schema migration.

Adding a field to a SQLModel model does NOT add the column to an existing
table (``create_all`` only creates missing tables). Without a migration the
deployed database answers "no such column" and every request fails.

The startup migration compares the model metadata with the real table
structure and adds the missing columns.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.db import session as db_session
from app.models.device import DeviceRecord, EngineRecord


def _table_columns(path: str, table: str) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    finally:
        connection.close()


def test_migration_adds_a_missing_column(tmp_path):
    """A column that the model declares but the table lacks is added."""
    db_path = tmp_path / "legacy.db"

    # Simulate a database created before the column existed.
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE devices ("
        "uid VARCHAR PRIMARY KEY, serial VARCHAR, engine_id INTEGER, "
        "transport_kind VARCHAR, alias VARCHAR, model VARCHAR, brand VARCHAR, "
        "product VARCHAR, device VARCHAR, platform VARCHAR, status VARCHAR, "
        "is_rooted BOOLEAN, last_error VARCHAR, last_seen DATETIME, "
        "created_at DATETIME, updated_at DATETIME)"
    )
    connection.commit()
    connection.close()

    assert "is_network_device" not in _table_columns(str(db_path), "devices")

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    import asyncio

    asyncio.run(db_session._migrate_columns())

    columns = _table_columns(str(db_path), "devices")
    assert "is_network_device" in columns, "the missing column must be added"

    asyncio.run(engine.dispose())


def test_migration_is_idempotent(tmp_path):
    """Running the migration twice does not fail."""
    db_path = tmp_path / "twice.db"

    async def _prepare() -> None:
        async with db_session.engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    import asyncio

    asyncio.run(_prepare())
    asyncio.run(db_session._migrate_columns())
    asyncio.run(db_session._migrate_columns())

    columns = _table_columns(str(db_path), "devices")
    assert columns.count("is_network_device") == 1

    asyncio.run(engine.dispose())
