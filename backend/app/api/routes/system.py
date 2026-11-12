"""System-level diagnostics (environment prerequisites)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app.core.response import BaseResponse
from app.services.preflight import run_checks, summarize

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/check", response_model=BaseResponse[dict], summary="环境自检")
async def system_check() -> BaseResponse[dict]:
    """Report adb / asyncssh / Python / storage readiness with fixes."""
    checks = await asyncio.to_thread(run_checks, True)
    return BaseResponse.success(summarize(checks))
