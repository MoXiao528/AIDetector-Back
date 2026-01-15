"""Schemas for parsing uploaded files."""

from pydantic import Field

from app.schemas.base import SchemaBase


class ParsedFileResult(SchemaBase):
    file_name: str = Field(..., example="example.pdf")
    content: str | None = Field(default=None, example="Extracted content")
    error: str | None = Field(default=None, example="Unsupported file type")


class ParseFilesResponse(SchemaBase):
    results: list[ParsedFileResult]
