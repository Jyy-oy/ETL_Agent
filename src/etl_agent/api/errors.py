"""Stable API error payloads."""

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorPayload(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        """创建带业务错误码、HTTP 状态码和扩展详情的 API 异常。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def request_id_for(request: Request) -> str:
    """从请求上下文读取请求 ID，未设置时返回安全的兜底值。"""
    return getattr(request.state, "request_id", "unknown")


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """将业务异常转换为统一格式的 JSON 错误响应。"""
    payload = ErrorPayload(
        code=exc.code,
        message=exc.message,
        request_id=request_id_for(request),
        details=exc.details,
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """将请求参数校验异常转换为包含字段位置的 422 响应。"""
    details = {
        "errors": [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", "invalid")}
            for error in exc.errors()
        ]
    }
    payload = ErrorPayload(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        request_id=request_id_for(request),
        details=details,
    )
    return JSONResponse(status_code=422, content=payload.model_dump())


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """将框架 HTTP 异常转换为统一格式并保留原 HTTP 状态码。"""
    payload = ErrorPayload(
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
        request_id=request_id_for(request),
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """将未处理异常隐藏内部细节并返回统一的 500 响应。"""
    # 只记录请求 ID 和堆栈供服务端排障，响应仍不暴露数据库、凭据或内部实现细节。
    logger.exception("Unhandled API error request_id=%s", request_id_for(request), exc_info=exc)
    payload = ErrorPayload(
        code="INTERNAL_ERROR",
        message="An internal error occurred",
        request_id=request_id_for(request),
    )
    return JSONResponse(status_code=500, content=payload.model_dump())
