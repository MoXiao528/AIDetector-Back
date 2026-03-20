from typing import Any

from pydantic import Field

from app.schemas.base import SchemaBase


class HealthResponse(SchemaBase):
    status: str = Field(..., json_schema_extra={"example": "ok"})


class ReadinessResponse(SchemaBase):
    status: str = Field(..., json_schema_extra={"example": "ok"})
    database: str = Field(..., json_schema_extra={"example": "ok"})
    detect_service: str = Field(..., json_schema_extra={"example": "ok"})


class WelcomeResponse(SchemaBase):
    message: str = Field(..., json_schema_extra={"example": "Welcome to AIDetector API"})


class DatabasePingResponse(SchemaBase):
    status: str = Field(..., json_schema_extra={"example": "ok"})


class ErrorResponse(SchemaBase):
    code: str | int = Field(..., json_schema_extra={"example": "NOT_FOUND"})
    message: str = Field(..., json_schema_extra={"example": "Not Found"})
    detail: Any = Field(..., json_schema_extra={"example": "Not Found"})
