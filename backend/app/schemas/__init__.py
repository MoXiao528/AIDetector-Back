"""Pydantic schema package."""

from app.schemas.api_key import (
    APIKeyBase,
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyResponse,
    APIKeySelfTestResponse,
    APIKeyStatus,
)
from app.schemas.admin import AdminStatusResponse
from app.schemas.analysis import AnalysisResponse, Citation, DetectRequest, SentenceAnalysis
from app.schemas.parse_files import ParseFilesResponse, ParsedFileResult
from app.schemas.detection import DetectionItem, DetectionListResponse, DetectionRequest, DetectionResponse
from app.schemas.auth import LoginRequest, RegisterRequest, Token, TokenPayload
from app.schemas.responses import DatabasePingResponse, ErrorResponse, HealthResponse, WelcomeResponse
from app.schemas.user import UserBase, UserCreate, UserProfile, UserProfileUpdate, UserResponse
from app.schemas.team import (
    TeamCreateRequest,
    TeamMemberCreateRequest,
    TeamMemberResponse,
    TeamResponse,
    TeamStatsItem,
    TeamStatsResponse,
)
from app.core.roles import UserRole

__all__ = [
    "APIKeyBase",
    "APIKeyCreateRequest",
    "APIKeyCreateResponse",
    "APIKeyResponse",
    "APIKeySelfTestResponse",
    "APIKeyStatus",
    "AdminStatusResponse",
    "AnalysisResponse",
    "Citation",
    "DetectionItem",
    "DetectionListResponse",
    "DetectionRequest",
    "DetectionResponse",
    "DetectRequest",
    "DatabasePingResponse",
    "ErrorResponse",
    "HealthResponse",
    "LoginRequest",
    "RegisterRequest",
    "TeamCreateRequest",
    "TeamMemberCreateRequest",
    "TeamMemberResponse",
    "TeamResponse",
    "TeamStatsItem",
    "TeamStatsResponse",
    "Token",
    "TokenPayload",
    "SentenceAnalysis",
    "ParseFilesResponse",
    "ParsedFileResult",
    "UserBase",
    "UserCreate",
    "UserProfile",
    "UserProfileUpdate",
    "UserResponse",
    "UserRole",
    "WelcomeResponse",
]
