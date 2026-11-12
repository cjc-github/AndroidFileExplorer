"""Tests for the environment diagnostics endpoint and missing-adb hint."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.db import session as db_session
from app.main import app
from app.services import preflight


def run_sync(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    db_session.engine = engine
    db_session.async_session_maker = maker

    async def _prepare() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    run_sync(_prepare())
    with TestClient(app) as test_client:
        yield test_client
    run_sync(engine.dispose())


def test_system_check_returns_actionable_checks(client):
    response = client.get("/api/v1/system/check")
    assert response.status_code == 200
    data = response.json()["data"]
    assert isinstance(data["ok"], bool)
    checks = {item["key"]: item for item in data["checks"]}
    assert {"python", "backend_deps", "asyncssh", "adb", "storage"} <= set(checks)
    for item in checks.values():
        assert isinstance(item["ok"], bool)
        assert isinstance(item["required"], bool)
        assert item["label"]
        assert item["message"]


def test_missing_adb_reports_the_install_hint(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)
    items = preflight.run_checks(deep=False)
    adb = next(item for item in items if item["key"] == "adb")
    assert adb["ok"] is False
    assert adb["required"] is False
    assert "platform-tools" in adb["fix"] or "ADB_PATH" in adb["fix"]
