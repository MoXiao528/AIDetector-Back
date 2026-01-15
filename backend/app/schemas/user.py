from datetime import datetime

from pydantic import EmailStr, Field, ConfigDict

from app.schemas.base import SchemaBase

from app.core.roles import UserRole


class UserBase(SchemaBase):
    email: EmailStr = Field(..., example="user@example.com")
    role: UserRole = Field(default=UserRole.INDIVIDUAL, example=UserRole.INDIVIDUAL)
    is_active: bool = Field(default=True, example=True)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, example="StrongPass!23")


class UserResponse(UserBase):
    id: int = Field(..., example=1)
    created_at: datetime = Field(...)

    model_config = ConfigDict(from_attributes=True)
