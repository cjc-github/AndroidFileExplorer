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
    # sqlite3.connect(timeout=...) is the busy timeout: a contending writer
    # waits up to 10s for the lock instead of failing with "database is
    # locked" immediately.  WAL mode is set once in init_db (it is a
    # persistent per-database property).
    connect_args={"timeout": 10.0} if settings.database_url.startswith("sqlite") else {},
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


async def _dedupe_engines(connection) -> None:
    """Delete duplicate engine rows (same kind/host/port), keep the oldest.

    Older deployments could register the same machine twice; the unique
    index created afterwards would refuse to build over such duplicates.
    Devices stay attached: their engine_id is repointed at the survivor.
    """
    rows = (
        await connection.execute(text("SELECT id, kind, host, port FROM engines ORDER BY id"))
    ).fetchall()
    if not rows:
        return
    kept: dict[tuple[str, str, int], int] = {}
    for engine_id, kind, host, port in rows:
        key = (str(kind or ""), str(host or ""), int(port or 0))
        survivor = kept.get(key)
        if survivor is None:
            kept[key] = engine_id
            continue
        await connection.execute(
            text("UPDATE devices SET engine_id = :keep WHERE engine_id = :dup"),
            {"keep": survivor, "dup": engine_id},
        )
        await connection.execute(
            text("DELETE FROM engines WHERE id = :id"), {"id": engine_id}
        )
        logger.info(f"Deduplicated engine rows for {host}:{port} (kept id={survivor})")


async def _create_engine_unique_index(connection) -> None:
    """Enforce one row per machine across restarts and old databases.

    The API rejects duplicates too; the index is the backstop that holds
    even when rows are written by an older build or directly.
    """
    try:
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_engines_kind_host_port "
                "ON engines(kind, host, port)"
            )
        )
    except Exception as exc:  # noqa: BLE001 - never block the startup here
        logger.warning(f"could not create the engines unique index: {exc}")


async def _enable_wal() -> None:
    """Turn on WAL mode for the SQLite database file (once, at startup).

    WAL lets the concurrent per-machine sync tasks write while readers are
    active instead of failing with "database is locked".  It is a persistent
    per-database property, so setting it here covers every later connection.

    The pragma must run outside a transaction ("cannot change into wal mode
    from within a transaction"), hence the AUTOCOMMIT connection.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        result = await autocommit.execute(text("PRAGMA journal_mode=WAL"))
        value = str(result.scalar() or "").lower()
    if value == "wal":
        logger.info("SQLite journal mode set to WAL")
    else:
        logger.warning(f"SQLite journal mode is {value!r} (WAL not applied)")


async def init_db() -> None:
    """Create the tables if they do not exist and align their columns."""
    await _enable_wal()
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    await _migrate_columns()
    async with engine.begin() as connection:
        await _dedupe_engines(connection)
        await _create_engine_unique_index(connection)
