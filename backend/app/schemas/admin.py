"""管理员相关的 Pydantic 模型。"""

from pydantic import Field

from app.schemas.base import SchemaBase


class AdminStatusResponse(SchemaBase):
    message: str = Field(..., example="admin ok")
