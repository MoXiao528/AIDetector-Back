"""API Key 相关的 Pydantic 模型。"""

from datetime import datetime
from enum import Enum

from pydantic import Field, ConfigDict

from app.schemas.base import SchemaBase


class APIKeyStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class APIKeyCreateRequest(SchemaBase):
    name: str = Field(..., min_length=1, max_length=255, json_schema_extra={"example": "CLI access"})


class APIKeyBase(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    name: str = Field(..., json_schema_extra={"example": "CLI access"})
    status: APIKeyStatus = Field(..., json_schema_extra={"example": APIKeyStatus.ACTIVE})
    created_at: datetime = Field(..., json_schema_extra={"example": "2024-01-01T00:00:00Z"})
    last_used_at: datetime | None = Field(default=None, json_schema_extra={"example": "2024-01-02T12:00:00Z"})

    model_config = ConfigDict(from_attributes=True)


class APIKeyResponse(APIKeyBase):
    ...


class APIKeyCreateResponse(APIKeyBase):
    key: str = Field(..., json_schema_extra={"example": "ak_xxxxxxxxxxxxx"})


class APIKeySelfTestResponse(SchemaBase):
    message: str = Field(..., json_schema_extra={"example": "API key is valid"})
