"""管理员相关的 Pydantic 模型。"""

from pydantic import BaseModel, Field


class AdminStatusResponse(BaseModel):
    message: str = Field(..., example="admin ok")
