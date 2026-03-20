from typing import Any

from pydantic import Field

from app.schemas.base import SchemaBase


class HealthResponse(SchemaBase):
    """健康检查响应模型。"""

    status: str = Field(..., json_schema_extra={"example": "ok"})


class WelcomeResponse(SchemaBase):
    """根路由欢迎响应模型。"""

    message: str = Field(..., json_schema_extra={"example": "Welcome to AIDetector API"})


class DatabasePingResponse(SchemaBase):
    """数据库连通性检查响应。"""

    status: str = Field(..., json_schema_extra={"example": "ok"})


class ErrorResponse(SchemaBase):
    """统一错误响应模型。"""

    code: str | int = Field(..., json_schema_extra={"example": "NOT_FOUND"})
    message: str = Field(..., json_schema_extra={"example": "Not Found"})
    detail: Any = Field(..., json_schema_extra={"example": "Not Found"})
