"""Async SQLite session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings
from app.logger import logger

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one database session per request."""
    async with async_session_maker() as session:
        yield session


def _default_for(column) -> str:
    """SQL literal for the default value of a new column."""
    python_type = getattr(column, "type", None)
    type_name = type(python_type).__name__.lower() if python_type else ""

    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
    if "bool" in type_name:
        return "0"
    if "int" in type_name:
        return "0"
    if "float" in type_name:
        return "0"
    return "NULL"


async def _table_columns(connection, table_name: str) -> set[str]:
    """Column names of a table, read with plain SQL (works inside a transaction)."""
    result = await connection.execute(text(f'PRAGMA table_info("{table_name}")'))
    return {row[1] for row in result.fetchall()}


async def _table_names(connection) -> set[str]:
    """Names of the existing tables."""
    result = await connection.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    return {row[0] for row in result.fetchall()}


async def _migrate_columns() -> None:
    """Add columns that the model declares but the database does not have.

    ``SQLModel.metadata.create_all`` creates missing TABLES but never adds a
    column to an existing one, so adding a field to a model would break a
    deployed database with "no such column". This lightweight migration keeps
    the schema in sync without pulling in a migration framework.
    """
    async with engine.begin() as connection:
        existing_tables = await _table_names(connection)

        for table_name, table in SQLModel.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            known = await _table_columns(connection, table_name)
            for column in table.columns:
                if column.name in known:
                    continue
                default = _default_for(column)
                sql_type = (
                    column.type.compile(dialect=engine.dialect)
                    if column.type is not None
                    else "TEXT"
                )
                logger.info(
                    f"Migrating {table_name}: adding column {column.name} {sql_type}"
                )
                await connection.execute(
                    text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" '
                        f"{sql_type} DEFAULT {default}"
                    )
                )


async def init_db() -> None:
    """Create the tables if they do not exist and align their columns."""
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    await _migrate_columns()
