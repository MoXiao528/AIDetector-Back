"""认证相关的 Pydantic 模型。"""

from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import SchemaBase


class RegisterRequest(SchemaBase):
    email: EmailStr = Field(..., example="user@example.com")
    name: str | None = Field(default=None, example="Alex Chen")
    password: str = Field(..., min_length=8, example="StrongPass!23")


class LoginRequest(SchemaBase):
    identifier: str = Field(..., example="user@example.com")
    password: str = Field(..., min_length=8, example="StrongPass!23")


class Token(SchemaBase):
    access_token: str = Field(..., example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...")
    token_type: str = Field(default="bearer", example="bearer")


class TokenPayload(SchemaBase):
    sub: str | None = None
    exp: int | None = None
    iat: datetime | None = None
    sub_type: str | None = None
    guest_id: str | None = None
