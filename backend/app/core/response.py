"""Standard HTTP response envelope shared by every route."""

from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ResponseCode(int, Enum):
    """Business status codes."""

    SUCCESS = 200
    CREATED = 201
    BAD_REQUEST = 400
    FORBIDDEN = 403
    NOT_FOUND = 404
    UNPROCESSABLE = 422
    INTERNAL_ERROR = 500
    UPSTREAM_ERROR = 502


class BaseResponse(BaseModel, Generic[T]):
    """Uniform response body: {code, message, data, timestamp}."""

    code: int = Field(default=ResponseCode.SUCCESS)
    message: str = Field(default="操作成功")
    data: T | None = Field(default=None)
    timestamp: datetime = Field(default_factory=datetime.now)

    @classmethod
    def success(cls, data: T | None = None, message: str = "操作成功") -> "BaseResponse[T]":
        return cls(code=ResponseCode.SUCCESS, message=message, data=data)

    @classmethod
    def error(
        cls,
        message: str,
        code: int = ResponseCode.INTERNAL_ERROR,
        data: T | None = None,
    ) -> "BaseResponse[T]":
        return cls(code=code, message=message, data=data)

    model_config = {"from_attributes": True}
