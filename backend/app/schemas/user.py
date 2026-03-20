from datetime import datetime

from pydantic import EmailStr, Field, ConfigDict

from app.schemas.base import SchemaBase

from app.core.roles import UserRole


class UserBase(SchemaBase):
    email: EmailStr = Field(..., json_schema_extra={"example": "user@example.com"})
    name: str = Field(..., json_schema_extra={"example": "henry_zhan"})
    role: UserRole = Field(default=UserRole.INDIVIDUAL, json_schema_extra={"example": UserRole.INDIVIDUAL})
    is_active: bool = Field(default=True, json_schema_extra={"example": True})


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, json_schema_extra={"example": "StrongPass!23"})


class UserProfile(SchemaBase):
    firstName: str | None = Field(default=None, json_schema_extra={"example": "Jane"})
    surname: str | None = Field(default=None, json_schema_extra={"example": "Doe"})
    organization: str | None = Field(default=None, json_schema_extra={"example": "Acme Inc."})
    role: str | None = Field(default=None, json_schema_extra={"example": "Engineer"})
    industry: str | None = Field(default=None, json_schema_extra={"example": "Technology"})


class UserProfileUpdate(SchemaBase):
    firstName: str | None = Field(default=None, json_schema_extra={"example": "Jane"})
    surname: str | None = Field(default=None, json_schema_extra={"example": "Doe"})
    organization: str | None = Field(default=None, json_schema_extra={"example": "Acme Inc."})
    role: str | None = Field(default=None, json_schema_extra={"example": "Engineer"})
    industry: str | None = Field(default=None, json_schema_extra={"example": "Technology"})


class UserResponse(UserBase):
    id: int = Field(..., json_schema_extra={"example": 1})
    created_at: datetime = Field(...)
    profile: UserProfile | None = None
    credits: int = Field(..., json_schema_extra={"example": 30000}, description="当前剩余点数（total - used）。")

    model_config = ConfigDict(from_attributes=True)
