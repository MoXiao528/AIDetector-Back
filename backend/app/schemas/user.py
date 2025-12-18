from datetime import datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    ADMIN = "ADMIN"


class UserBase(BaseModel):
    email: EmailStr = Field(..., example="user@example.com")
    role: UserRole = Field(default=UserRole.INDIVIDUAL, example=UserRole.INDIVIDUAL)
    is_active: bool = Field(default=True, example=True)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, example="StrongPass!23")


class UserResponse(UserBase):
    id: int = Field(..., example=1)
    created_at: datetime = Field(...)

    class Config:
        from_attributes = True
