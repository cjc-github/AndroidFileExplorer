"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import AuthTokenMiddleware
from app.api.request_id import RequestIdMiddleware
from app.api.routes import devices, engines, files
from app.api.routes import settings as settings_routes
from app.config import settings
from app.core.response import BaseResponse
from app.db.session import init_db
from app.logger import logger, setup_logging
from app.services.registry import DeviceRegistry
from app.state import get_transport_resolver, sync_known_engines

setup_logging(settings.log_level)


async def _auto_sync_loop() -> None:
    """Discover devices on a schedule (and run the auto-root hook).

    The interval lives in the platform settings; ``0`` disables the loop, so
    changing the setting takes effect without a restart.  Every failure is
    logged and swallowed: the loop must survive a machine going away.
    """
    from app.db.session import async_session_maker
    from app.api.routes.devices import _sync_all_machines
    from app.services.settings_service import SettingsService
    from app.services.transport_resolver import TransportResolver

    while True:
        try:
            async with async_session_maker() as session:
                info = await SettingsService(session).read()
                interval = info.auto_sync_seconds
            if interval > 0:
                async with async_session_maker() as session:
                    # _sync_all_machines already runs the auto-root hook after
                    # the discovery pass; calling it again would reboot adbd
                    # twice per cycle.
                    await _sync_all_machines(session, get_transport_resolver())
                logger.info(f"auto sync cycle finished (interval {interval}s)")
                await asyncio.sleep(interval)
            else:
                # Disabled: re-check the setting every few seconds so that
                # enabling it takes effect without a restart.
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive anything
            logger.exception("auto sync cycle failed")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare the database and the local engine row."""
    settings.ensure_dirs()
    await init_db()
    logger.info("Database initialized")

    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        registry = DeviceRegistry(session)
        await registry.ensure_local_engine()

    await sync_known_engines()
    logger.info("Local engine registered")

    auto_sync_task = asyncio.create_task(_auto_sync_loop())
    logger.info("Auto sync loop started")

    try:
        yield
    finally:
        auto_sync_task.cancel()
        try:
            await auto_sync_task
        except asyncio.CancelledError:
            pass
    await get_transport_resolver().close()
    logger.info("AndroidFileExplorer backend stopped")


app = FastAPI(
    title="android-file-explorer-api",
    description="Android 设备发现 / 文件浏览 / 文件传输 / 运行状态监控",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware order matters: the LAST added runs OUTERMOST.  CORS must see
# the requests first, because a browser preflight (OPTIONS) never carries
# the Authorization header and would be rejected by the auth check - and it
# must also add its headers to every answer, including a 401.
# The request id sits between CORS and the auth check: the auth middleware
# answers a 401 on its own, without reaching the inner stack, so a request
# id placed inside it would never stamp the rejected request.
app.add_middleware(AuthTokenMiddleware, token=settings.api_auth_token)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(engines.router)
app.include_router(settings_routes.router)
app.include_router(devices.router)
app.include_router(files.router)


@app.get("/api/v1/health", response_model=BaseResponse[dict], summary="健康检查")
async def health() -> BaseResponse[dict]:
    """Liveness probe."""
    return BaseResponse.success({"status": "ok", "service": "android-file-explorer"})


def main() -> None:
    """Run the API server."""
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
