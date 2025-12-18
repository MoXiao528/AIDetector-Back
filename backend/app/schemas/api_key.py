"""API Key 相关的 Pydantic 模型。"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class APIKeyStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, example="CLI access")


class APIKeyBase(BaseModel):
    id: int = Field(..., example=1)
    name: str = Field(..., example="CLI access")
    status: APIKeyStatus = Field(..., example=APIKeyStatus.ACTIVE)
    created_at: datetime = Field(..., example="2024-01-01T00:00:00Z")
    last_used_at: datetime | None = Field(default=None, example="2024-01-02T12:00:00Z")

    class Config:
        from_attributes = True


class APIKeyResponse(APIKeyBase):
    ...


class APIKeyCreateResponse(APIKeyBase):
    key: str = Field(..., example="ak_xxxxxxxxxxxxx")


class APIKeySelfTestResponse(BaseModel):
    message: str = Field(..., example="API key is valid")
