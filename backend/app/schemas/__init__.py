"""Pydantic schema package."""

from app.schemas.api_key import (
    APIKeyBase,
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyResponse,
    APIKeySelfTestResponse,
    APIKeyStatus,
)
from app.schemas.detection import DetectionItem, DetectionListResponse, DetectionRequest, DetectionResponse
from app.schemas.auth import LoginRequest, RegisterRequest, Token, TokenPayload
from app.schemas.responses import DatabasePingResponse, ErrorResponse, HealthResponse, WelcomeResponse
from app.schemas.user import UserBase, UserCreate, UserResponse, UserRole

__all__ = [
    "APIKeyBase",
    "APIKeyCreateRequest",
    "APIKeyCreateResponse",
    "APIKeyResponse",
    "APIKeySelfTestResponse",
    "APIKeyStatus",
    "DetectionItem",
    "DetectionListResponse",
    "DetectionRequest",
    "DetectionResponse",
    "DatabasePingResponse",
    "ErrorResponse",
    "HealthResponse",
    "LoginRequest",
    "RegisterRequest",
    "Token",
    "TokenPayload",
    "UserBase",
    "UserCreate",
    "UserResponse",
    "UserRole",
    "WelcomeResponse",
]
