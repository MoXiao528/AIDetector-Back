from pydantic import BaseModel, Field


class DatabasePingResponse(BaseModel):
    """数据库连通性检测响应模型。"""

    status: str = Field(..., example="ok")
