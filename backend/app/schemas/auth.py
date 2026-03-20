"""认证相关的 Pydantic 模型。"""

from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import SchemaBase


class RegisterRequest(SchemaBase):
    email: EmailStr = Field(..., json_schema_extra={"example": "user@example.com"})
    name: str | None = Field(default=None, json_schema_extra={"example": "Alex Chen"})
    password: str = Field(..., min_length=8, json_schema_extra={"example": "StrongPass!23"})


class LoginRequest(SchemaBase):
    identifier: str = Field(..., json_schema_extra={"example": "user@example.com"})
    password: str = Field(..., min_length=8, json_schema_extra={"example": "StrongPass!23"})


class GuestTokenRequest(SchemaBase):
    guest_id: str | None = Field(default=None, min_length=1, max_length=64, json_schema_extra={"example": "9c8bf2e9-47b2-47d5-8b4a-04ae0fca0d3f"})


class Token(SchemaBase):
    access_token: str = Field(..., json_schema_extra={"example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."})
    token_type: str = Field(default="bearer", json_schema_extra={"example": "bearer"})
    guest_id: str | None = Field(default=None, json_schema_extra={"example": "9c8bf2e9-47b2-47d5-8b4a-04ae0fca0d3f"})


class TokenPayload(SchemaBase):
    sub: str | None = None
    exp: int | None = None
    iat: datetime | None = None
    sub_type: str | None = None
    guest_id: str | None = None
