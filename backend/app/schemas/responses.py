from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应模型。"""

    status: str = Field(..., example="ok")


class WelcomeResponse(BaseModel):
    """根路由欢迎响应模型。"""

    message: str = Field(..., example="Welcome to AIDetector API")


class DatabasePingResponse(BaseModel):
    """数据库连通性检查响应。"""

    status: str = Field(..., example="ok")


class ErrorResponse(BaseModel):
    """统一错误响应模型。"""

    code: int = Field(..., example=404)
    message: str = Field(..., example="Not Found")
    detail: Any = Field(..., example="Not Found")
