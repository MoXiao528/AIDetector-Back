from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """用户基础字段。"""

    email: EmailStr = Field(..., example="user@example.com")
    role: str = Field(default="INDIVIDUAL", example="INDIVIDUAL")
    is_active: bool = Field(default=True, example=True)
    created_at: datetime | None = Field(default=None, example="2024-01-01T00:00:00Z")

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    """创建用户请求模型。"""

    email: EmailStr = Field(..., example="user@example.com")
    password: str = Field(..., min_length=8, example="strong-password", repr=False)


class UserRead(UserBase):
    """用户响应模型。"""

    id: int = Field(..., example=1)
