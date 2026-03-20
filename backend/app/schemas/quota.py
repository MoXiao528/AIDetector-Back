from pydantic import Field

from app.schemas.base import SchemaBase


class QuotaResponse(SchemaBase):
    actor_type: str = Field(..., json_schema_extra={"example": "user"})
    limit: int = Field(..., json_schema_extra={"example": 30000})
    used_today: int = Field(..., json_schema_extra={"example": 1200})
    remaining: int = Field(..., json_schema_extra={"example": 28800})
