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


class UserProfile(SchemaBase):
    firstName: str | None = Field(default=None, example="Jane")
    surname: str | None = Field(default=None, example="Doe")
    organization: str | None = Field(default=None, example="Acme Inc.")
    role: str | None = Field(default=None, example="Engineer")
    industry: str | None = Field(default=None, example="Technology")


class UserProfileUpdate(SchemaBase):
    firstName: str | None = Field(default=None, example="Jane")
    surname: str | None = Field(default=None, example="Doe")
    organization: str | None = Field(default=None, example="Acme Inc.")
    role: str | None = Field(default=None, example="Engineer")
    industry: str | None = Field(default=None, example="Technology")


class UserResponse(UserBase):
    id: int = Field(..., example=1)
    created_at: datetime = Field(...)
    profile: UserProfile | None = None

    model_config = ConfigDict(from_attributes=True)
