from pydantic import Field

from app.schemas.base import SchemaBase


class QuotaResponse(SchemaBase):
    actor_type: str = Field(..., example="user")
    limit: int = Field(..., example=30000)
    used_today: int = Field(..., example=1200)
    remaining: int = Field(..., example=28800)
