"""Schemas for parsing uploaded files."""

from pydantic import Field

from app.schemas.base import SchemaBase


class ParsedFileResult(SchemaBase):
    file_name: str = Field(..., json_schema_extra={"example": "example.pdf"})
    content: str | None = Field(default=None, json_schema_extra={"example": "Extracted content"})
    error: str | None = Field(default=None, json_schema_extra={"example": "Unsupported file type"})


class ParseFilesResponse(SchemaBase):
    results: list[ParsedFileResult]
