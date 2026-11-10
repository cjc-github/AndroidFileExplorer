"""Platform settings API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BaseResponse
from app.db.session import get_session
from app.schemas.settings import SettingsInfo, SettingsUpdate
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("", response_model=BaseResponse[SettingsInfo], summary="查询平台设置")
async def read_settings(db: AsyncSession = Depends(get_session)) -> BaseResponse[SettingsInfo]:
    """Return the effective platform settings."""
    return BaseResponse.success(await SettingsService(db).read())


@router.put("", response_model=BaseResponse[SettingsInfo], summary="修改平台设置")
async def update_settings(
    payload: SettingsUpdate,
    db: AsyncSession = Depends(get_session),
) -> BaseResponse[SettingsInfo]:
    """Replace the platform settings; takes effect without a restart."""
    return BaseResponse.success(await SettingsService(db).update(payload), message="设置已保存")
